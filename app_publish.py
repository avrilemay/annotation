import streamlit as st
import pandas as pd
import pickle, hashlib, io, json, html, re, bz2
from pathlib import Path

APP_TITLE = "Annotation implicite articles ↔ décisions"
PICKLE_INDEX_PATH = "decision_index.pkl.bz2"    # chemin relatif et compressé
AUTOSAVE_PATH = "annotations_autosave.xlsx"

st.set_page_config(APP_TITLE, layout="wide")
st.title(APP_TITLE)

# ----------------------------------------------------------------------------
# 1. Upload du fichier d'annotations
uploaded = st.file_uploader(
    "Charger votre fichier XLSX d'annotations à faire (obligatoire)", type=["xlsx"]
)
if not uploaded:
    st.warning("Merci d’uploader votre fichier Excel avant de commencer.")
    st.stop()

file_bytes = uploaded.read()
file_hash  = hashlib.md5(file_bytes).hexdigest()
data_buf   = io.BytesIO(file_bytes)

# ----------------------------------------------------------------------------
# 2. Init session
if (
    "df" not in st.session_state
    or st.session_state.get("file_hash") != file_hash
):
    st.session_state["file_hash"] = file_hash
    df = pd.read_excel(data_buf)

    for col in ("implicit", "revoir"):
        if col not in df.columns:
            df[col] = ""
    df["implicit"] = df["implicit"].fillna("")
    df["revoir"]   = df["revoir"].fillna("")

    if "decision_id" not in df.columns:
        st.error("Il manque la colonne 'decision_id'.")
        st.stop()

    df[["implicit", "revoir"]] = df[["implicit", "revoir"]].astype("object")
    st.session_state["df"]  = df
    st.session_state["ptr"] = 0

df  = st.session_state["df"]
ptr = st.session_state["ptr"]

# ----------------------------------------------------------------------------
# 3. Cache index pickle (compressé bz2)
@st.cache_resource
def get_decision_index():
    try:
        with bz2.open(PICKLE_INDEX_PATH, "rb") as f:
            return pickle.load(f)
    except Exception as e:
        st.error(f"Impossible de charger l'index des décisions : {e}")
        return {}

def get_num_date(decision_id):
    try:
        return tuple(decision_id.split("__"))
    except Exception:
        return None, None

def load_full_text(num, date):
    index = get_decision_index()
    path = index.get((num, date))
    if path:
        # les fichiers JSON sont dans un sous-dossier "decisions/"
        path_obj = Path("decisions") / Path(path).name
        try:
            with open(path_obj, "r", encoding="utf-8") as f:
                return json.load(f).get("text", "")
        except Exception as e:
            return f"Détails de la décision introuvables ({e})"
    return "Décision introuvable."

def render_full_text(full, chunk):
    """
    Retourne du HTML avec le chunk surligné même si espaces, ponctuation ou sauts de lignes diffèrent.
    Recherche très robuste basée uniquement sur les mots.
    """
    escaped_full = html.escape(full)
    escaped_chunk = html.escape(chunk.strip())
    chunk_words = re.findall(r'\w+', escaped_chunk)
    pattern = r'\W+'.join(map(re.escape, chunk_words))
    match = re.search(pattern, escaped_full, flags=re.IGNORECASE | re.DOTALL)
    if match:
        start, end = match.span()
        highlighted = (
            escaped_full[:start]
            + f"<mark style='background-color: #FFDD00;'>{escaped_full[start:end]}</mark>"
            + escaped_full[end:]
        )
        return highlighted.replace("\n", "<br>")
    return escaped_full.replace("\n", "<br>")

# ----------------------------------------------------------------------------
# 4. Lignes restantes à annoter
mask = (df["implicit"] == "") | (df["revoir"] == "Oui")
df_todo = df[mask]

st.sidebar.metric("Total",     len(df))
st.sidebar.metric("Restantes", len(df_todo))

if ptr >= len(df_todo):
    st.session_state["ptr"] = 0
    ptr = 0

if df_todo.empty:
    st.success("🎉 Toutes les annotations sont terminées !")
    st.stop()

row = df_todo.iloc[ptr]
idx = row.name

# ----------------------------------------------------------------------------
# 5. Layout dynamique : 1 colonne quand la décision est masquée, 2 colonnes quand affichée

row = df_todo.iloc[ptr]
idx = row.name

# --- clés uniques ---
show_key = f"show_decision_{idx}"
rep_key  = f"rep_{idx}"

# Init toggle décision
if show_key not in st.session_state:
    st.session_state[show_key] = False

# Init radio sur "Non" si pas encore défini
if rep_key not in st.session_state:
    st.session_state[rep_key] = "Non"

# --- Layout dynamique : 1 ou 2 colonnes ---
if st.session_state[show_key]:
    col_left, col_right = st.columns([1, 1], gap="medium")
else:
    col_left = st.container()
    col_right = None

with col_left:
    st.markdown(f"### Article {row['pred_art']}")
    st.write(row["article_text"])

    st.markdown("### Chunk à annoter")
    st.write(row["text"])

    st.markdown("### L'article est‑il appliqué implicitement?")

    # Form pour radio + save
    with st.form(key=f"form_{idx}"):
        reponse = st.radio(
            "Choisissez une option",
            ("Oui", "Non", "À revoir", "Je ne sais pas"),
            horizontal=True,
            key=rep_key  # garde la valeur dans session_state
        )

        save_clicked = st.form_submit_button("Enregistrer et passer au suivant", type="primary")
        if save_clicked:
            if reponse == "À revoir":
                st.session_state["df"].at[idx, "revoir"] = "Oui"
                st.session_state["ptr"] += 1
            else:
                st.session_state["df"].at[idx, "implicit"] = reponse
                st.session_state["df"].at[idx, "revoir"]   = ""
            try:
                st.session_state["df"].to_excel(AUTOSAVE_PATH, index=False)
            except Exception as e:
                st.warning(f"Autosave KO: {e}")
            st.rerun()

    # --- Bouton toggle décision SOUS la question ---
    toggle_label = "▶︎ Afficher la décision" if not st.session_state[show_key] else "◀︎ Masquer la décision"
    if st.button(toggle_label, key=f"btn_toggle_{idx}"):
        st.session_state[show_key] = not st.session_state[show_key]
        st.rerun()

# ---------------------- Colonne droite : décision scollable (seulement si visible) ----------------------
if col_right:
    num, date = get_num_date(row["decision_id"])
    full_text_raw = load_full_text(num, date)
    full_text_html = render_full_text(full_text_raw, row["text"])

    with col_right:
        st.markdown("### Décision complète")
        st.markdown(
            f"""
            <div style='border:1px solid #ccc; padding:1rem; height:calc(100vh - 180px); overflow-y:auto;'>
                {full_text_html}
            </div>
            """,
            unsafe_allow_html=True
        )


# ----------------------------------------------------------------------------
# 9. Téléchargement fichier mis à jour
buf = io.BytesIO()
st.session_state["df"].to_excel(buf, index=False)
st.sidebar.download_button(
    "Télécharger le fichier mis à jour",
    buf.getvalue(),
    "mes_annotations_mises_a_jour.xlsx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
