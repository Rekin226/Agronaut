"""Read-only bundled field guidance; no farm records or model calls."""

import streamlit as st

from agronaut_agent.field_guides import guide_catalog, guide_languages, read_field_guide


def render_field_guides():
    with st.expander("Field guides"):
        languages = guide_languages()
        language = st.selectbox("Language / Langue / 語言", list(languages), format_func=languages.get,
                                key="field_guide_language")
        try:
            titles = {guide["topic"]: guide["title"] for guide in guide_catalog(language)}
            label = {"en": "Guide topic", "fr": "Sujet du guide", "zh-Hant": "指南主題"}[language]
            # Translated labels change Streamlit's widget identity. Retain the
            # topic ID explicitly instead of moving back to the first guide.
            topics = list(titles)
            current = st.session_state.get("field_guide_topic", topics[0])
            topic = st.selectbox(label, list(titles), format_func=titles.get,
                                 index=topics.index(current) if current in titles else 0, key="field_guide_topic")
            guide = read_field_guide(topic, language)
        except ValueError as exc:
            st.error(str(exc))
            return
        st.markdown(guide["content_markdown"])
        download_label = {"en": "Download this guide", "fr": "Télécharger ce guide", "zh-Hant": "下載本指南"}[language]
        st.download_button(download_label, guide["content_markdown"].encode("utf-8"),
                           file_name=f"agronaut-guide-{topic}-{language}.md",
                           mime="text/markdown; charset=utf-8", key="field_guide_download")
