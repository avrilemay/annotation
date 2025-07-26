import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import pickle, hashlib, io, json, html, re, bz2
from pathlib import Path
import os

# -----------------------------------------------------------------------------
# Constantes globales
# -----------------------------------------------------------------------------
APP_TITLE = "Annotation implicite articles ↔ décisions"
PICKLE_INDEX_PATH = "decision_index.pkl.bz2"    # Index de correspondance vers JSON (compressé)
AUTOSAVE_PATH = "annotations_autosave.xlsx"      # Fichier autosave local

# -----------------------------------------------------------------------------
# Configuration de la page Streamlit
# -----------------------------------------------------------------------------
st.set_page_config(APP_TITLE, layout="wide")
st.title(APP_TITLE)

# -----------------------------------------------------------------------------
# 1. Upload du fichier d'annotations utilisateur
# -----------------------------------------------------------------------------
uploaded = st.file_uploader(
    "Charger votre fichier XLSX d'annotations à faire (obligatoire)", type=["xlsx"]
)
if not uploaded:
    # Stoppe l'app tant qu'aucun fichier n'a été uploadé
    st.warning("Merci d’uploader votre fichier Excel avant de commencer.")
    st.stop()

# On lit le contenu du fichier uploadé dans un buffer mémoire
file_bytes = uploaded.read()
file_hash  = hashlib.md5(file_bytes).hexdigest()   # hash pour suivre si nouveau fichier chargé
data_buf   = io.BytesIO(file_bytes)

# -----------------------------------------------------------------------------
# 2. Initialisation du DataFrame en session (une seule fois par fichier)
# -----------------------------------------------------------------------------
if (
    "df" not in st.session_state
    or st.session_state.get("file_hash") != file_hash
):
    st.session_state["file_hash"] = file_hash
    df = pd.read_excel(data_buf)

    # S'assure que les colonnes nécessaires existent, les crée si besoin
    for col in ("implicit", "revoir"):
        if col not in df.columns:
            df[col] = ""
    df["implicit"] = df["implicit"].fillna("")
    df["revoir"]   = df["revoir"].fillna("")

    # Vérifie la présence de l'identifiant unique de décision
    if "decision_id" not in df.columns:
        st.error("Il manque la colonne 'decision_id'.")
        st.stop()

    # Force le type string/object pour ces colonnes
    df[["implicit", "revoir"]] = df[["implicit", "revoir"]].astype("object")
    st.session_state["df"]  = df
    st.session_state["ptr"] = 0    # index du chunk courant à afficher

df  = st.session_state["df"]
ptr = st.session_state["ptr"]

# -----------------------------------------------------------------------------
# 3. Chargement de l'index des décisions (Pickle bz2, cache Streamlit)
# -----------------------------------------------------------------------------
@st.cache_resource
def get_decision_index():
    """Charge l'index (num, date) → chemin JSON, ou renvoie un dict vide si erreur."""
    try:
        with bz2.open(PICKLE_INDEX_PATH, "rb") as f:
            return pickle.load(f)
    except Exception as e:
        st.error(f"Impossible de charger l'index des décisions\u202f: {e}")
        return {}

def get_num_date(decision_id: str):
    """Extrait (numéro, date) depuis decision_id, séparés par '__'."""
    try:
        return tuple(decision_id.split("__"))
    except Exception:
        return None, None

def load_full_text(num, date):
    """Récupère le texte complet JSON correspondant à (num, date), sinon message d'erreur."""
    index = get_decision_index()
    path = index.get((num, date))
    if path:
        path_obj = Path("decisions") / Path(path).name
        try:
            with open(path_obj, "r", encoding="utf-8") as f:
                return json.load(f).get("text", "")
        except Exception as e:
            return f"Détails de la décision introuvables ({e})"
    return "Décision introuvable."

def render_full_text(full: str, chunk: str) -> str:
    """
    Surligne le chunk dans le texte complet, même si ponctuation/espaces changent.
    - Échappe HTML.
    - Pattern : tous les mots du chunk séparés par '\W+'.
    - Remplace \n par <br> pour l'affichage web.
    """
    escaped_full  = html.escape(full)
    escaped_chunk = html.escape(chunk.strip())
    chunk_words   = re.findall(r'\w+', escaped_chunk)
    pattern       = r'\W+'.join(map(re.escape, chunk_words))
    match = re.search(pattern, escaped_full, flags=re.IGNORECASE | re.DOTALL)
    if match:
        start, end = match.span()
        highlighted = (
            escaped_full[:start]
            + "<mark id='chunk-highlight' style='background-color:#FFDD00;'>"
            + escaped_full[start:end]
            + "</mark>"
            + escaped_full[end:]
        )
        return highlighted.replace("\n", "<br>")
    return escaped_full.replace("\n", "<br>")

def render_decision_panel(full_text_html: str):
    """
    Affiche le texte complet de la décision dans un panneau scrollable, sombre,
    avec le chunk surligné, en HTML pur (pour meilleure mise en page).
    """
    components.html(
        f"""
        <html>
        <head>
            <meta charset=\"utf-8\" />
            <style>
              html, body {{
                background-color: #121212;
                color: #ddd;
                margin: 0;
                padding: 0;
                font-size: 0.95rem;
                line-height: 1.4;
                font-family: system-ui, -apple-system, \"Segoe UI\", Roboto, sans-serif;
              }}
              #decision-box {{
                background-color: #121212;
                color: #ddd;
                border: 1px solid #444;
                padding: 1rem;
                height: calc(100vh - 180px);
                overflow-y: auto;
              }}
              mark#chunk-highlight {{
                background: #FFDD00;
                color: #000;
                padding: 0 2px;
              }}
            </style>
        </head>
        <body>
          <div id=\"decision-box\">{full_text_html}</div>
          <script>
            // Scroll automatiquement jusqu'au chunk surligné
            const box    = document.getElementById('decision-box');
            const target = box ? box.querySelector('#chunk-highlight') : null;
            if (box && target) {{
                box.scrollTop = target.offsetTop - 20;
            }}
          </script>
        </body>
        </html>
        """,
        height=780,
        scrolling=False
    )

# -----------------------------------------------------------------------------
# 4. Filtrage des lignes à annoter (reste à traiter)
# -----------------------------------------------------------------------------
# Une ligne doit être ré-annotée si "implicit" est vide OU si "revoir" vaut "Oui"
mask    = (df["implicit"] == "") | (df["revoir"] == "Oui")
df_todo = df[mask]

# Affiche dans la sidebar le nombre total et le nombre restant
st.sidebar.metric("Total",     len(df))
st.sidebar.metric("Restantes", len(df_todo))

# -----------------------------------------------------------------------------
# 5. Préparation du nom du fichier téléchargé (toujours propre)
# -----------------------------------------------------------------------------
import re as regex  # Pour éviter le conflit avec re déjà importé

if uploaded is not None and hasattr(uploaded, 'name'):
    base_name = os.path.splitext(uploaded.name)[0]  # Nom sans extension
    # Supprime tout suffixe _XXX_left à la fin pour éviter les doublons
    base_name = regex.sub(r'_\d+_left$', '', base_name)
else:
    base_name = "mes_annotations"

nb_left = len(df_todo)
dl_filename = f"{base_name}_{nb_left}_left.xlsx"  # Ex : "fichier_12_left.xlsx"

# Sauvegarde le fichier dans un buffer en mémoire
buf = io.BytesIO()
st.session_state["df"].to_excel(buf, index=False)

# Bouton de téléchargement (toujours visible dans la sidebar)
st.sidebar.download_button(
    "Télécharger le fichier mis à jour",
    buf.getvalue(),
    dl_filename,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# -----------------------------------------------------------------------------
# 6. Fin des annotations : message + gros bouton download central
# -----------------------------------------------------------------------------
if df_todo.empty:
    st.success("🎉 Toutes les annotations sont terminées\u202f!")
    st.download_button(
        "Télécharger le fichier mis à jour",
        buf.getvalue(),
        dl_filename,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    st.stop()

# -----------------------------------------------------------------------------
# 7. Préparation des index/états pour l'affichage du chunk courant
# -----------------------------------------------------------------------------
# Si on a dépassé la dernière ligne, on revient au début (cas rare)
if ptr >= len(df_todo):
    st.session_state["ptr"] = 0
    ptr = 0

row = df_todo.iloc[ptr]
idx = row.name

# -----------------------------------------------------------------------------
# 8. Gestion des clés d'état pour chaque chunk (décision/annotation)
# -----------------------------------------------------------------------------
show_key = f"show_decision_{idx}"
rep_key  = f"rep_{idx}"

# On garde en session si la décision est affichée ou non (toggle)
if show_key not in st.session_state:
    st.session_state[show_key] = False

# Par défaut, la radio "Non" est sélectionnée
if rep_key not in st.session_state:
    st.session_state[rep_key] = "Non"

show_decision = st.session_state[show_key]

layout_placeholder = st.empty()

# -----------------------------------------------------------------------------
# 9. Affichage du panneau d'annotation (colonne gauche)
# -----------------------------------------------------------------------------
def render_left_panel(container):
    """Affichage du chunk à annoter + options dans la colonne de gauche."""
    with container:
        st.markdown(f"### Article {row['pred_art']}")
        st.write(row["article_text"])

        st.markdown("### Chunk à annoter")
        st.write(row["text"])

        st.markdown("### L'article est‑il appliqué implicitement?")

        # Formulaire annotation : radio + bouton save
        with st.form(key=f"form_{idx}"):
            reponse = st.radio(
                "Choisissez une option",
                ("Oui", "Non", "À revoir", "Je ne sais pas"),
                horizontal=True,
                key=rep_key
            )

            save_clicked = st.form_submit_button("Enregistrer et passer au suivant", type="primary")
            if save_clicked:
                if reponse == "À revoir":
                    st.session_state["df"].at[idx, "revoir"] = "Oui"
                    st.session_state["ptr"] += 1  # Passe au suivant
                else:
                    st.session_state["df"].at[idx, "implicit"] = reponse
                    st.session_state["df"].at[idx, "revoir"]   = ""
                try:
                    # Sauvegarde immédiate locale (sécurité)
                    st.session_state["df"].to_excel(AUTOSAVE_PATH, index=False)
                except Exception as e:
                    st.warning(f"Autosave KO: {e}")
                st.rerun()  # Recharge l'app pour afficher la ligne suivante

        # Bouton pour afficher/masquer la décision complète
        toggle_label = "▶︎ Afficher la décision" if not st.session_state[show_key] else "◀︎ Masquer la décision"
        if st.button(toggle_label, key=f"btn_toggle_{idx}"):
            st.session_state[show_key] = not st.session_state[show_key]
            st.rerun()

# -----------------------------------------------------------------------------
# 10. Affichage du layout principal (colonne annot/colonne décision)
# -----------------------------------------------------------------------------
if show_decision:
    # Si la décision doit être affichée, deux colonnes
    with layout_placeholder.container():
        col_left, col_right = st.columns([1, 1], gap="medium")
        render_left_panel(col_left)

        # Affichage décision complète (droite)
        num, date = get_num_date(row["decision_id"])
        full_text_raw  = load_full_text(num, date)
        full_text_html = render_full_text(full_text_raw, row["text"])

        with col_right:
            st.markdown("### Décision complète")
            render_decision_panel(full_text_html)
else:
    # Sinon, seulement la colonne de gauche
    with layout_placeholder.container():
        col_left = st.container()
        render_left_panel(col_left)
