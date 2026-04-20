import streamlit as st
import anthropic
import requests
import json
import math
import re

st.set_page_config(page_title="カタログ テキスト生成ツール", page_icon="🏠", layout="wide")

st.markdown("""
<style>
[data-testid="stSidebar"] { background: #1c1c1c; }
[data-testid="stSidebar"] * { color: #ddd !important; }
.block-container { padding-top: 1.5rem; }
h3 { color: #b85c2a; border-left: 3px solid #b85c2a; padding-left: 8px; font-size: 13px;
     letter-spacing: 2px; text-transform: uppercase; margin-bottom: 12px; }
</style>
""", unsafe_allow_html=True)

# ── ヘルパー関数 ──────────────────────────────────────────────────────────────

def m2_to_tsubo(m2):
    return round(m2 / 3.3058, 2) if m2 and m2 > 0 else None

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def to_walk_min(meters):
    return math.ceil(meters / 80)

def get_name(tags):
    return tags.get("name:ja") or tags.get("name") or tags.get("brand")

def fetch_nearby(address):
    try:
        geo_res = requests.get(
            "https://msearch.gsi.go.jp/address-search/AddressSearch",
            params={"q": address},
            timeout=10
        )
        geo_res.raise_for_status()
        geo = geo_res.json()
    except requests.exceptions.JSONDecodeError:
        return None, "住所検索サービスの応答が不正です。しばらく待ってから再試行してください。"
    except requests.exceptions.RequestException as e:
        return None, f"住所検索の通信エラー: {e}"

    if not geo:
        return None, "住所が見つかりませんでした。都道府県から入力してください。"

    coords = geo[0]["geometry"]["coordinates"]
    lon, lat = float(coords[0]), float(coords[1])
    radius = 1200
    query = f"""[out:json][timeout:30];
(
  node["shop"~"supermarket|convenience|grocery|department_store|mall"](around:{radius},{lat},{lon});
  way["shop"~"supermarket|convenience|grocery|department_store|mall"](around:{radius},{lat},{lon});
  node["amenity"~"school|kindergarten|university|college"](around:{radius},{lat},{lon});
  way["amenity"~"school|kindergarten|university|college"](around:{radius},{lat},{lon});
  node["amenity"~"hospital|clinic|doctors|pharmacy|dentist"](around:{radius},{lat},{lon});
  way["amenity"~"hospital|clinic|doctors|pharmacy|dentist"](around:{radius},{lat},{lon});
  node["leisure"~"park|garden|playground"](around:{radius},{lat},{lon});
  way["leisure"~"park|garden|playground"](around:{radius},{lat},{lon});
);
out center;"""

    overpass_endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.openstreetmap.ru/api/interpreter",
    ]
    ov = None
    last_error = None
    for endpoint in overpass_endpoints:
        try:
            ov_res = requests.post(endpoint, data=query, timeout=30)
            ov_res.raise_for_status()
            ov = ov_res.json()
            break
        except Exception as e:
            last_error = e
            continue
    if ov is None:
        return None, f"周辺施設検索に失敗しました（全サーバーが混雑中）。しばらく待ってから再試行してください。"
    shops, edus, meds, parks = [], [], [], []

    for el in ov.get("elements", []):
        tags = el.get("tags", {})
        name = get_name(tags)
        if not name:
            continue
        el_lat = el.get("lat") or (el.get("center") or {}).get("lat")
        el_lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if not el_lat or not el_lon:
            continue
        dist = haversine(lat, lon, el_lat, el_lon)
        entry = {"name": name, "dist": dist, "walk": to_walk_min(dist)}
        amenity = tags.get("amenity", "")
        if tags.get("shop"):
            shops.append(entry)
        elif re.search(r"school|kindergarten|university|college", amenity):
            edus.append(entry)
        elif re.search(r"hospital|clinic|doctors|pharmacy|dentist", amenity):
            meds.append(entry)
        elif tags.get("leisure"):
            parks.append(entry)

    def fmt(arr, n):
        return "\n".join(
            f"{i+1}. {e['name']} 徒歩{e['walk']}分"
            for i, e in enumerate(sorted(arr, key=lambda x: x["dist"])[:n])
        )

    return {"shop": fmt(shops, 5), "edu": fmt(edus, 4), "med": fmt(meds, 4),
            "park": fmt(parks, 4), "total": len(shops)+len(edus)+len(meds)+len(parks)}, None

def call_ai(api_key, prompt, max_tokens=1500):
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    text = msg.content[0].text
    match = re.search(r'\{[\s\S]*\}', text)
    if not match:
        raise ValueError("JSONが取得できませんでした")
    return json.loads(match.group(0))

def build_jsx(data_obj):
    lines = []
    for key, val in data_obj.items():
        if val is not None and str(val).strip():
            esc = str(val).replace("\\","\\\\").replace('"','\\"').replace("\r\n","\\n").replace("\n","\\n").replace("\r","\\n")
            lines.append(f'        data["{key}"] = "{esc}";')
    body = "\n".join(lines)
    return f"""(function () {{
    var doc;
    try {{ doc = app.activeDocument; }}
    catch(e) {{ alert("Open Illustrator file first."); return; }}
    var data = {{}};
{body}
    var count = 0, notFound = [];
    for (var key in data) {{
        if (!data.hasOwnProperty(key)) continue;
        var matched = false;
        for (var i = 0; i < doc.textFrames.length; i++) {{
            try {{
                var frame = doc.textFrames[i];
                if (frame.locked || frame.hidden) continue;
                if (frame.contents.replace(/\\s/g,'') === key.replace(/\\s/g,'')) {{
                    frame.contents = data[key]; count++; matched = true;
                }}
            }} catch(e) {{}}
        }}
        if (!matched) notFound.push(key);
    }}
    if (count === 0) {{
        alert("No placeholders found. Check @ markers in Illustrator.");
    }} else {{
        var result = "Done! " + count + " items updated.";
        if (notFound.length > 0) result += "\\nNot found:\\n" + notFound.join("\\n");
        alert(result);
    }}
}})();"""

def fetch_nearest_station(lat, lon):
    query = f"""[out:json][timeout:20];
(
  node["railway"~"station|halt"](around:3000,{lat},{lon});
);
out body;"""
    overpass_endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.openstreetmap.ru/api/interpreter",
    ]
    for endpoint in overpass_endpoints:
        try:
            res = requests.post(endpoint, data=query, timeout=25)
            res.raise_for_status()
            data = res.json()
            break
        except Exception:
            data = None
    if not data:
        return None

    stations = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name:ja") or tags.get("name")
        if not name:
            continue
        dist = haversine(lat, lon, el["lat"], el["lon"])
        operator = tags.get("operator", "")
        stations.append({"name": name, "dist": dist, "walk": to_walk_min(dist), "operator": operator})

    if not stations:
        return None
    nearest = sorted(stations, key=lambda x: x["dist"])[0]
    prefix = f"{nearest['operator']}「" if nearest["operator"] else "「"
    return f"{prefix}{nearest['name']}」駅 徒歩{nearest['walk']}分"

def fetch_nearby_detailed(lat, lon):
    radius = 1200
    query = f"""[out:json][timeout:30];
(
  node["shop"="supermarket"](around:{radius},{lat},{lon});
  way["shop"="supermarket"](around:{radius},{lat},{lon});
  node["shop"="convenience"](around:{radius},{lat},{lon});
  way["shop"="convenience"](around:{radius},{lat},{lon});
  node["amenity"~"school|kindergarten|university|college"](around:{radius},{lat},{lon});
  way["amenity"~"school|kindergarten|university|college"](around:{radius},{lat},{lon});
  node["amenity"~"hospital|clinic|doctors|pharmacy|dentist"](around:{radius},{lat},{lon});
  way["amenity"~"hospital|clinic|doctors|pharmacy|dentist"](around:{radius},{lat},{lon});
  node["amenity"="bank"](around:{radius},{lat},{lon});
  node["leisure"~"park|playground"](around:{radius},{lat},{lon});
  way["leisure"~"park|playground"](around:{radius},{lat},{lon});
);
out center;"""

    overpass_endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.openstreetmap.ru/api/interpreter",
    ]
    ov = None
    for endpoint in overpass_endpoints:
        try:
            res = requests.post(endpoint, data=query, timeout=35)
            res.raise_for_status()
            ov = res.json()
            break
        except Exception:
            continue
    if not ov:
        return None, "周辺施設検索に失敗しました。しばらく待ってから再試行してください。"

    supers, convenis, edus, meds = [], [], [], []
    for el in ov.get("elements", []):
        tags = el.get("tags", {})
        name = get_name(tags)
        if not name:
            continue
        el_lat = el.get("lat") or (el.get("center") or {}).get("lat")
        el_lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if not el_lat or not el_lon:
            continue
        dist = haversine(lat, lon, el_lat, el_lon)
        entry = {"name": name, "dist": dist, "walk": to_walk_min(dist)}
        shop    = tags.get("shop", "")
        amenity = tags.get("amenity", "")
        leisure = tags.get("leisure", "")
        if shop == "supermarket":
            supers.append(entry)
        elif shop == "convenience":
            convenis.append(entry)
        elif re.search(r"school|kindergarten|university|college", amenity):
            edus.append(entry)
        elif re.search(r"hospital|clinic|doctors|pharmacy|dentist|bank", amenity) or leisure:
            meds.append(entry)

    def fmt_cat(arr, n):
        return "\r".join(f"{e['name']} 徒歩{e['walk']}分"
                         for e in sorted(arr, key=lambda x: x["dist"])[:n])

    parts = []
    if supers:   parts.append("【スーパー】\r"   + fmt_cat(supers,   3))
    if convenis: parts.append("【コンビニ】\r"   + fmt_cat(convenis, 3))
    if edus:     parts.append("【教育】\r"       + fmt_cat(edus,     3))
    if meds:     parts.append("【医療・その他】\r" + fmt_cat(meds,     4))

    return "\r\r".join(parts), None

def build_jsx_step2(filename, data_obj):
    lines = []
    for key, val in data_obj.items():
        if val is not None and str(val).strip():
            esc = str(val).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\r")
            lines.append(f'        "@{key}"      : "{esc}",')
    body = "\n".join(lines)
    return f"""/*
  {filename}
  データ流し込み用
*/

(function() {{
    var doc = app.activeDocument;
    var count = 0;

    var data = {{
{body}
    }};

    for (var i = 0; i < doc.textFrames.length; i++) {{
        try {{
            var frame = doc.textFrames[i];
            if (frame.locked || frame.hidden) continue;
            if (data[frame.name] !== undefined) {{
                frame.contents = data[frame.name];
                count++;
            }}
        }} catch(e) {{}}
    }}

    if (count === 0) {{
        alert("書き換わった場所がありませんでした。\\nレイヤー名（@titleなど）が正しいか確認してください。");
    }} else {{
        alert("完了！\\n" + count + " 箇所のデータを更新しました。(o^―^o)ﾆｺ");
    }}
}})();"""

def render_result_tab(page_label, results, jsx_filename):
    if not results:
        st.info("← 「① 入力」タブで情報を入力し、生成してください")
        return

    st.caption("✏️ テキストは直接編集できます")
    edited = {}
    for key, val in results.items():
        if val is None or str(val).strip() == "":
            continue
        col_label, col_input = st.columns([1, 3])
        with col_label:
            st.markdown(f"`{key}`")
        with col_input:
            edited[key] = st.text_area(
                label=key,
                value=str(val),
                label_visibility="collapsed",
                key=f"edit_{page_label}_{key}",
                height=80
            )

    st.markdown("---")
    st.markdown("**Illustrator での使い方：** ファイル → スクリプト → その他のスクリプト → ダウンロードした .jsx を選択")
    jsx_content = build_jsx(edited)
    st.download_button(
        label=f"⬇ {jsx_filename} をダウンロード",
        data=jsx_content.encode("utf-8"),
        file_name=jsx_filename,
        mime="text/plain",
        use_container_width=True,
        type="primary"
    )

# ── session state 初期化 ──────────────────────────────────────────────────────

for k in ["results_p1", "results_p23", "results_p4", "results_p5"]:
    if k not in st.session_state:
        st.session_state[k] = {}

# ── サイドバー ────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## ⚙️ 設定")

    default_key = ""
    try:
        default_key = st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        default_key = st.session_state.get("api_key", "")

    api_key_input = st.text_input("Anthropic API Key", value=default_key, type="password", placeholder="sk-ant-api03-...")
    if api_key_input:
        st.session_state["api_key"] = api_key_input
        if api_key_input.startswith("sk-ant-"):
            st.success("✓ APIキー設定済み")
        else:
            st.warning("⚠ 形式を確認してください")

    st.markdown("---")
    st.markdown("""**使い方**
1. APIキーを入力
2. ①入力タブで情報を入力
3. 生成ボタンを押す
4. 各ページタブで確認・編集
5. .jsxをダウンロードして実行""")

# ── メインタブ ────────────────────────────────────────────────────────────────

tab_input, tab_p1, tab_p23, tab_p4, tab_p5, tab_step2 = st.tabs(["① 入力", "P1 表紙", "P2-3 エリア", "P4 区画", "P5 物件", "② Step2スクリプト"])

with tab_input:

    # 基本情報
    st.markdown("### 基本情報")
    col1, col2 = st.columns(2)
    with col1:
        name_jp     = st.text_input("物件名（日本語）", placeholder="例: 奈良市右京３丁目IV")
        madori      = st.text_input("間取り", placeholder="例: 2LDK＋WIC＋カースペース2台付")
    with col2:
        name_en     = st.text_input("英語タイトル", placeholder="例: NARA - UKYO")
        total_units = st.text_input("総戸数", placeholder="例: 限定1棟")

    # 交通アクセス
    st.markdown("### 交通アクセス")
    col1, col2 = st.columns(2)
    with col1:
        station     = st.text_input("路線名・駅名", placeholder="例: 近鉄橿原線 大和西大寺駅")
    with col2:
        walk_min_v  = st.number_input("徒歩時間（分）", min_value=0, max_value=99, value=0)
    col1, col2, col3 = st.columns(3)
    with col1:
        time_kyoto   = st.number_input("京都まで（分）", min_value=0, value=0)
    with col2:
        time_tennoji = st.number_input("天王寺まで（分）", min_value=0, value=0)
    with col3:
        time_namba   = st.number_input("難波まで（分）", min_value=0, value=0)

    # 周辺施設
    st.markdown("### 周辺施設")
    address = st.text_input("所在地（OSM自動取得に使用）", placeholder="例: 奈良県奈良市右京3丁目")

    if st.button("🗺 住所から周辺施設を自動取得（OpenStreetMap）", use_container_width=True):
        if not address:
            st.warning("先に「所在地」を入力してください")
        else:
            with st.spinner("周辺施設を検索中…"):
                result, error = fetch_nearby(address)
            if error:
                st.error(error)
            else:
                st.session_state["osm_shop"] = result["shop"]
                st.session_state["osm_edu"]  = result["edu"]
                st.session_state["osm_med"]  = result["med"]
                st.session_state["osm_park"] = result["park"]
                st.success(f"✅ {result['total']}件取得完了（内容を確認・編集してください）")
                st.rerun()

    col1, col2 = st.columns(2)
    with col1:
        fac_shop = st.text_area("🛒 ショッピング・生活施設",
            value=st.session_state.get("osm_shop", ""),
            placeholder="例:\n1. イオン 徒歩10分\n2. スーパー○○ 徒歩5分")
        fac_med  = st.text_area("🏥 医療施設",
            value=st.session_state.get("osm_med", ""),
            placeholder="例:\n1. ○○クリニック 徒歩5分")
    with col2:
        fac_edu  = st.text_area("🎓 教育機関",
            value=st.session_state.get("osm_edu", ""),
            placeholder="例:\n1. ○○小学校 徒歩10分\n2. ○○中学校 徒歩12分")
        fac_park = st.text_area("🌳 公園・レジャー",
            value=st.session_state.get("osm_park", ""),
            placeholder="例:\n1. ○○公園 徒歩3分")

    notes = st.text_area("アピールポイント（任意）", placeholder="例: 閑静な住宅街、南向き、角地など")

    # 区画・面積情報
    st.markdown("### 区画・面積情報")
    col1, col2 = st.columns(2)
    with col1:
        youto       = st.text_input("用途地域", placeholder="例: 第一種住居地域")
        price       = st.number_input("販売価格（万円）", min_value=0, value=0)
        shikichi_m2 = st.number_input("敷地面積（㎡）", min_value=0.0, value=0.0, step=0.01)
        shikichi_tb = m2_to_tsubo(shikichi_m2)
        if shikichi_tb:
            st.caption(f"✦ {shikichi_tb} 坪")
        tatemono_m2 = st.number_input("建物面積（㎡）", min_value=0.0, value=0.0, step=0.01)
        tatemono_tb = m2_to_tsubo(tatemono_m2)
        if tatemono_tb:
            st.caption(f"✦ {tatemono_tb} 坪")
    with col2:
        madori_type = st.text_input("間取りタイプ", placeholder="例: 3LDKタイプ")
        kouzou      = st.text_input("構造", placeholder="例: 木造2階建")
        kenpei      = st.text_input("建ぺい率／容積率", placeholder="例: 40%／60%")
        seller      = st.text_input("売主", placeholder="例: ○○株式会社")
        sekou       = st.text_input("施工", placeholder="例: ○○株式会社")

    col1, col2, col3 = st.columns(3)
    with col1:
        kodo    = st.text_input("高度地区", placeholder="例: 高度地区")
        kuiki   = st.text_input("区域指定", placeholder="例: 市街化区域")
        chimoku = st.text_input("地目", placeholder="例: 宅地")
    with col2:
        bouka   = st.text_input("防火指定", placeholder="例: なし")
        kenri   = st.text_input("土地権利", placeholder="例: 所有権")
        kansei  = st.text_input("完成時期", placeholder="例: お問い合わせください")
    with col3:
        fukuin    = st.text_input("幅員／接道", placeholder="例: 公道 北側6m")
        gakku_sho = st.text_input("学区（小学校）", placeholder="例: 奈良市立○○小学校区")
        gakku_chu = st.text_input("学区（中学校）", placeholder="例: 奈良市立○○中学校区")

    # ローン情報
    st.markdown("### ローン情報（P5用）")
    col1, col2 = st.columns(2)
    with col1:
        bank    = st.text_input("銀行名", placeholder="例: 南都銀行")
        hensai  = st.number_input("返済期間（年）", min_value=0, value=0)
    with col2:
        kinri   = st.text_input("金利（%）", placeholder="例: 0.775")
        monthly = st.number_input("月々の支払例（円）", min_value=0, value=0)

    # 生成ボタン
    st.markdown("---")
    if st.button("✦ AIでテキストをすべて生成する", type="primary", use_container_width=True):
        api_key = st.session_state.get("api_key", "")
        if not api_key:
            st.error("サイドバーでAPIキーを入力してください")
        elif not name_jp or not station or not walk_min_v:
            st.error("「物件名」「駅名」「徒歩時間」は必須です")
        else:
            progress = st.progress(0, text="P1（表紙）を生成中…")
            try:
                p1 = call_ai(api_key, f"""不動産チラシ表紙用テキスト。物件名:{name_jp} 英語:{name_en} 間取り:{madori}
JSONのみ返してください:
{{"@title_jp":"物件名（都市名と住所を改行\\nで区切る）","@title_en":"英語タイトル","@madori":"間取り説明"}}""")
                st.session_state["results_p1"] = p1
            except Exception as e:
                st.error(f"P1エラー: {e}")

            progress.progress(25, text="P2-3（エリア情報）を生成中…")
            try:
                p23 = call_ai(api_key, f"""不動産チラシP2-3用テキスト生成。
物件名:{name_jp} 駅:{station} 徒歩{walk_min_v}分
京都{time_kyoto}分/天王寺{time_tennoji}分/難波{time_namba}分
ショッピング:{fac_shop or 'なし'} 教育:{fac_edu or 'なし'} 医療:{fac_med or 'なし'} 公園:{fac_park or 'なし'}
アピール:{notes or 'なし'}
JSONのみ返してください:
{{"@title_en_bg":"背景英字","@area_catch":"右ページ見出し","@intro":"物件紹介文3〜4文改行あり","@env":"生活環境2〜3文","@access_txt":"交通アクセス2〜3文"}}""", 2000)
                st.session_state["results_p23"] = {
                    **p23,
                    "@shops": fac_shop, "@edu": fac_edu, "@med": fac_med, "@parks": fac_park,
                    "@time_kyoto": str(time_kyoto), "@time_tennoji": str(time_tennoji),
                    "@time_namba": str(time_namba), "@walk": f"{station} 徒歩{walk_min_v}分",
                }
            except Exception as e:
                st.error(f"P2-3エラー: {e}")

            progress.progress(50, text="P4（区画情報）を生成中…")
            try:
                p4ai = call_ai(api_key, f"""不動産の写真キャプション文を生成してください。
物件名:{name_jp} 周辺:{fac_shop or ''} {fac_park or ''} {notes or ''}
JSONのみ返してください: {{"@p4_comment":"写真下コメント。周辺の魅力を1〜2文で"}}""")
                p4_comment = p4ai.get("@p4_comment", "")
            except Exception:
                p4_comment = ""
            st.session_state["results_p4"] = {
                "@p4_title_en": name_en, "@p4_name": name_jp,
                "@p4_catch": f"新築分譲住宅 {total_units}", "@p4_comment": p4_comment,
                "@address": address, "@traffic": f"{station} 徒歩{walk_min_v}分",
                "@units": f"販売{total_units}区画／全{total_units}区画",
                "@price": f"{price}万", "@madori2": madori, "@kouzou": kouzou,
                "@shikichi": str(shikichi_m2), "@shikichi_tsubo": str(shikichi_tb),
                "@tatemono": str(tatemono_m2), "@tatemono_tsubo": str(tatemono_tb),
                "@youto": youto, "@kenpei": kenpei, "@kodo": kodo, "@bouka": bouka,
                "@kuiki": kuiki, "@kenri": kenri, "@fukuin": fukuin,
                "@sekou": sekou, "@seller": seller, "@kansei": kansei,
                "@gakku": f"{gakku_sho}／{gakku_chu}", "@chimoku": chimoku,
            }

            progress.progress(75, text="P5（物件情報）を生成中…")
            try:
                p5 = call_ai(api_key, f"""不動産チラシP5用テキスト。
物件名:{name_jp} 間取り:{madori_type or madori} 価格:{price}万円
月々:{monthly}円 ローン:{bank} {price}万/{kinri}%/{hensai}年 アピール:{notes}
JSONのみ返してください:
{{"@p5_catch":"上部キャッチ。「オール電化住宅『物件名』誕生！」形式","@p5_main":"大見出し。「◯つの理想を叶えるお家」","@p5_sub":"「－理想の◯◯・理想の◯◯・理想の◯◯－」","@p5_loan":"ローン条件1文"}}""")
                st.session_state["results_p5"] = {
                    **p5,
                    "@p5_price": f"{price}万円",
                    "@p5_monthly": f"{monthly:,}円" if monthly else "",
                    "@p5_madori": madori_type,
                    "@p5_shikichi": str(shikichi_m2), "@p5_shikichi_tsubo": str(shikichi_tb),
                    "@p5_tatemono": str(tatemono_m2), "@p5_tatemono_tsubo": str(tatemono_tb),
                    "@time_kyoto": str(time_kyoto), "@time_tennoji": str(time_tennoji),
                    "@time_namba": str(time_namba), "@walk": f"{station} 徒歩{walk_min_v}分",
                }
            except Exception as e:
                st.error(f"P5エラー: {e}")

            progress.progress(100, text="完了！")
            st.success("✅ すべて生成完了！各タブで確認してください")

with tab_p1:
    st.markdown("## PAGE 1 — 表紙")
    render_result_tab("p1", st.session_state["results_p1"], "P1_表紙_流し込み.jsx")

with tab_p23:
    st.markdown("## PAGE 2-3 — エリア情報")
    render_result_tab("p23", st.session_state["results_p23"], "P2-3_エリア情報_流し込み.jsx")

with tab_p4:
    st.markdown("## PAGE 4 — 区画情報")
    render_result_tab("p4", st.session_state["results_p4"], "P4_区画情報_流し込み.jsx")

with tab_p5:
    st.markdown("## PAGE 5 — 物件情報")
    render_result_tab("p5", st.session_state["results_p5"], "P5_物件情報_流し込み.jsx")

with tab_step2:
    st.markdown("## Step2 スクリプト生成（レイヤー名マッチング方式）")
    st.caption("Illustratorのレイヤー名（@titleなど）に直接データを流し込むスクリプトを生成します")

    col1, col2 = st.columns(2)
    with col1:
        s2_title      = st.text_input("① タイトル", placeholder="例: 奈良市右京３丁目６期１号地")
        s2_completion = st.text_input("② 完成時期", placeholder="例: 令和9年２月")
        s2_price      = st.text_input("③ 販売価格（万円）", placeholder="例: 4,980")
        s2_loan       = st.text_input("④ 月々支払い（円）", placeholder="例: 120,694")
        s2_address    = st.text_input("⑤ 所在地", placeholder="例: 奈良市右京３丁目7-8")
        s2_school1    = st.text_input("⑥ 小学校区", placeholder="例: 奈良市立ならやま小学校区")
        s2_school2    = st.text_input("⑦ 中学校区", placeholder="例: 奈良市立ならやま中学校区")
    with col2:
        s2_land       = st.text_input("⑨ 土地面積", placeholder="例: 169.24㎡（51.2坪）")
        s2_house      = st.text_input("⑩ 建物面積", placeholder="例: 104.10㎡（31.5坪）")
        s2_layout     = st.text_input("⑪ 間取り", placeholder="例: 3LDK")
        s2_parking    = st.text_input("⑫ 駐車場", placeholder="例: 2台")
        s2_filename   = st.text_input("ファイル名（.jsx）", placeholder="例: Step2_Ukyo3_6ki1go.jsx", value="Step2_流し込み.jsx")

    st.markdown("### ⑧ 交通 / ⑬ 周辺環境（自動リサーチ）")
    s2_addr_for_osm = st.text_input("住所（自動リサーチ用）", placeholder="例: 奈良県奈良市右京3丁目7-8",
                                     help="⑧交通と⑬周辺環境の自動取得に使います")

    if st.button("🗺 交通・周辺環境を自動取得", use_container_width=True):
        if not s2_addr_for_osm:
            st.warning("住所を入力してください")
        else:
            with st.spinner("住所を検索中…"):
                try:
                    geo_res = requests.get(
                        "https://msearch.gsi.go.jp/address-search/AddressSearch",
                        params={"q": s2_addr_for_osm}, timeout=10
                    )
                    geo_res.raise_for_status()
                    geo = geo_res.json()
                except Exception as e:
                    st.error(f"住所検索エラー: {e}")
                    geo = []

            if geo:
                coords = geo[0]["geometry"]["coordinates"]
                s2_lon, s2_lat = float(coords[0]), float(coords[1])

                with st.spinner("最寄り駅を検索中…"):
                    station_result = fetch_nearest_station(s2_lat, s2_lon)
                    if station_result:
                        st.session_state["s2_traffic"] = station_result
                    else:
                        st.warning("駅が見つかりませんでした")

                with st.spinner("周辺施設を検索中…"):
                    env_result, env_error = fetch_nearby_detailed(s2_lat, s2_lon)
                    if env_error:
                        st.error(env_error)
                    else:
                        st.session_state["s2_environment"] = env_result
                        st.success("✅ 自動取得完了（内容を確認・編集してください）")
                        st.rerun()
            else:
                st.error("住所が見つかりませんでした")

    col1, col2 = st.columns(2)
    with col1:
        s2_traffic = st.text_area("⑧ 交通", value=st.session_state.get("s2_traffic", ""),
                                   placeholder="例: 近鉄京都線「高の原」駅 徒歩14分", height=80)
    with col2:
        s2_environment = st.text_area("⑬ 周辺環境", value=st.session_state.get("s2_environment", ""),
                                       placeholder="【スーパー】\nイオン 徒歩10分\n\n【教育】\n○○小学校 徒歩5分",
                                       height=200)

    st.markdown("---")
    if st.button("⬇ Step2スクリプト（.jsx）を生成・ダウンロード", type="primary", use_container_width=True):
        data_obj = {
            "title":       s2_title,
            "completion":  s2_completion,
            "price":       s2_price,
            "loan":        s2_loan,
            "address":     s2_address,
            "school1":     s2_school1,
            "school2":     s2_school2,
            "traffic":     s2_traffic,
            "land_area":   s2_land,
            "house_area":  s2_house,
            "layout":      s2_layout,
            "parking":     s2_parking,
            "environment": s2_environment,
        }
        fname = s2_filename if s2_filename.endswith(".jsx") else s2_filename + ".jsx"
        jsx_content = build_jsx_step2(fname, data_obj)
        st.download_button(
            label=f"⬇ {fname} をダウンロード",
            data=jsx_content.encode("utf-8"),
            file_name=fname,
            mime="text/plain",
            use_container_width=True,
            type="primary",
            key="dl_step2"
        )
