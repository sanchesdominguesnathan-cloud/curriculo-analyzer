"""
app.py
Ponto de entrada da aplicação Streamlit — Análise Automática de Currículos.

Execução local:
    streamlit run app.py
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import io

import database as db
import parser as curriculo_parser
import score as score_module
import ranking
from keywords import listar_todas_palavras_chave_sugeridas, BASE_SINONIMOS, PESOS_PADRAO
from utils import gerar_hash_arquivo, gerar_resumo_candidato

st.set_page_config(page_title="Análise Automática de Currículos", layout="wide")
db.inicializar_banco()

if "resultados" not in st.session_state:
    st.session_state.resultados = []
if "df_resultados" not in st.session_state:
    st.session_state.df_resultados = pd.DataFrame()


def secao_cabecalho():
    st.title(" Análise Automática de Currículos")
    st.caption(
        "Faça upload dos currículos, descreva a vaga e cadastre as "
        "palavras-chave. O sistema extrai os dados, pontua e classifica "
        "os candidatos automaticamente."
    )


def secao_entrada():
    st.header("1. Vaga e Currículos")

    col_upload, col_vaga = st.columns([1, 1])

    with col_upload:
        arquivos = st.file_uploader(
            "Upload de currículos (PDF ou DOCX)",
            type=["pdf", "docx"],
            accept_multiple_files=True,
        )

    with col_vaga:
        descricao_vaga = st.text_area(
            "Descrição da vaga",
            placeholder="Ex: Buscamos um Analista Administrativo com experiência em "
                        "gestão de processos, domínio de Excel e Power BI...",
            height=150,
        )

    st.subheader("2. Palavras-chave")
    st.caption(
        "Digite as palavras-chave separadas por vírgula. Você pode ajustar o "
        "peso de cada uma na tabela abaixo. Sinônimos (ex: 'Planilhas' para "
        "'Excel') são reconhecidos automaticamente."
    )

    sugestoes = ", ".join(listar_todas_palavras_chave_sugeridas())

    col_obg, col_desej = st.columns(2)
    with col_obg:
        texto_obrigatorias = st.text_area(
            "🔴 Palavras-chave OBRIGATÓRIAS (separadas por vírgula)",
            placeholder=sugestoes,
            height=100,
            key="input_obrigatorias",
        )
    with col_desej:
        texto_desejaveis = st.text_area(
            "🟡 Palavras-chave DESEJÁVEIS (separadas por vírgula)",
            placeholder="Ex: Inglês, Liderança, Comunicação",
            height=100,
            key="input_desejaveis",
        )

    palavras_obrigatorias_lista = [p.strip() for p in texto_obrigatorias.split(",") if p.strip()]
    palavras_desejaveis_lista = [p.strip() for p in texto_desejaveis.split(",") if p.strip()]

    pesos_obrigatorias, pesos_desejaveis = {}, {}

    if palavras_obrigatorias_lista or palavras_desejaveis_lista:
        with st.expander("⚙️ Ajustar pesos das palavras-chave", expanded=False):
            st.caption("Peso de 1 a 10. Quanto maior o peso, mais essa palavra impacta a pontuação final.")
            if palavras_obrigatorias_lista:
                st.markdown("**Obrigatórias**")
                cols = st.columns(min(4, len(palavras_obrigatorias_lista)) or 1)
                for i, palavra in enumerate(palavras_obrigatorias_lista):
                    peso_padrao = PESOS_PADRAO.get(palavra.lower().strip(), 8)
                    with cols[i % len(cols)]:
                        pesos_obrigatorias[palavra] = st.slider(
                            palavra, 1, 10, peso_padrao, key=f"peso_obg_{palavra}"
                        )
            if palavras_desejaveis_lista:
                st.markdown("**Desejáveis**")
                cols = st.columns(min(4, len(palavras_desejaveis_lista)) or 1)
                for i, palavra in enumerate(palavras_desejaveis_lista):
                    peso_padrao = PESOS_PADRAO.get(palavra.lower().strip(), 5)
                    with cols[i % len(cols)]:
                        pesos_desejaveis[palavra] = st.slider(
                            palavra, 1, 10, peso_padrao, key=f"peso_dej_{palavra}"
                        )
    else:
        pesos_obrigatorias = {}
        pesos_desejaveis = {}

    if not pesos_obrigatorias:
        pesos_obrigatorias = {p: PESOS_PADRAO.get(p.lower().strip(), 8) for p in palavras_obrigatorias_lista}
    if not pesos_desejaveis:
        pesos_desejaveis = {p: PESOS_PADRAO.get(p.lower().strip(), 5) for p in palavras_desejaveis_lista}

    return arquivos, descricao_vaga, pesos_obrigatorias, pesos_desejaveis


def processar_analise(arquivos, descricao_vaga, pesos_obrigatorias, pesos_desejaveis):
    if not arquivos:
        st.warning("Envie ao menos um currículo antes de analisar.")
        return

    todas_palavras = list(set(list(pesos_obrigatorias.keys()) + list(pesos_desejaveis.keys())))
    if not todas_palavras:
        todas_palavras = listar_todas_palavras_chave_sugeridas()

    resultados = []
    barra = st.progress(0, text="Iniciando análise...")

    for i, arquivo in enumerate(arquivos):
        barra.progress((i) / len(arquivos), text=f"Analisando {arquivo.name}...")
        conteudo_bytes = arquivo.getvalue()
        hash_arquivo = gerar_hash_arquivo(conteudo_bytes)

        ja_analisado = db.curriculo_ja_analisado(hash_arquivo)
        if ja_analisado:
            st.info(f"⏭️ '{arquivo.name}' já havia sido analisado anteriormente. Reaproveitando resultado salvo.")
            resultados.append(ja_analisado)
            continue

        try:
            dados = curriculo_parser.processar_curriculo(arquivo.name, conteudo_bytes, todas_palavras)
        except ValueError as e:
            st.error(str(e))
            continue

        ajuste = db.calcular_ajuste_aprendizado(dados.get("competencias_encontradas", []))
        resultado_score = score_module.calcular_score_final(
            dados, pesos_obrigatorias, pesos_desejaveis, ajuste_aprendizado=ajuste
        )
        dados.update(resultado_score)
        dados["resumo"] = gerar_resumo_candidato(dados)
        dados["hash_arquivo"] = hash_arquivo
        dados["conteudo_bytes"] = conteudo_bytes

        id_banco = db.salvar_curriculo(hash_arquivo, arquivo.name, dados, descricao_vaga)
        dados["_id_banco"] = id_banco

        resultados.append(dados)

    barra.progress(1.0, text="Análise concluída!")
    st.session_state.resultados = resultados
    st.session_state.df_resultados = ranking.montar_tabela_resultados(resultados)


def secao_filtros(df: pd.DataFrame):
    st.header("4. Filtros")
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    with col1:
        cidade = st.text_input("Cidade")
    with col2:
        experiencia_min = st.number_input("Exp. mínima (meses)", min_value=0, value=0, step=1)
    with col3:
        idioma = st.text_input("Idioma")
    with col4:
        formacao = st.text_input("Formação")
    with col5:
        pontuacao_min = st.number_input("Pontuação mínima", min_value=0, max_value=100, value=0, step=5)
    with col6:
        cargo = st.text_input("Cargo/palavra no resumo")

    return ranking.aplicar_filtros(
        df,
        cidade=cidade or None,
        experiencia_min_meses=experiencia_min or None,
        idioma=idioma or None,
        formacao=formacao or None,
        pontuacao_minima=pontuacao_min or None,
        cargo=cargo or None,
    )


def cor_status(status):
    return {
        "Aprovado": "background-color: #d4edda",
        "Talvez": "background-color: #fff3cd",
        "Reprovado": "background-color: #f8d7da",
    }.get(status, "")


def secao_resultados(df_filtrado: pd.DataFrame):
    st.header("5. Resultados")
    if df_filtrado.empty:
        st.info("Nenhum resultado para exibir com os filtros atuais.")
        return

    colunas_exibicao = [
        "Ranking", "Nome", "Pontuação", "Compatibilidade (%)", "Experiência",
        "Formação", "Idiomas", "Cidade", "Status"
    ]
    df_exibicao = df_filtrado[colunas_exibicao]

    st.dataframe(
        df_exibicao.style.apply(
            lambda linha: [cor_status(linha["Status"])] * len(linha), axis=1
        ),
        use_container_width=True,
        height=420,
    )

    st.subheader("Detalhes e decisão por candidato")
    for _, linha in df_filtrado.iterrows():
        with st.expander(f"#{linha['Ranking']} — {linha['Nome']} ({linha['Status']}, {linha['Pontuação']} pts)"):
            colA, colB = st.columns([2, 1])
            with colA:
                st.markdown(f"**Resumo:** {linha['Resumo']}")
                st.markdown(f"**Email:** {linha['Email']} | **Telefone:** {linha['Telefone']}")
                st.markdown(f"**LinkedIn:** {linha['LinkedIn']}")
            with colB:
                candidato_original = next(
                    (r for r in st.session_state.resultados if r.get("arquivo") == linha["Arquivo"]), None
                )
                if candidato_original and candidato_original.get("conteudo_bytes"):
                    st.download_button(
                        "📎 Abrir currículo",
                        data=candidato_original["conteudo_bytes"],
                        file_name=linha["Arquivo"],
                        key=f"download_{linha['Arquivo']}_{linha['Ranking']}",
                    )
                col_ap, col_rep = st.columns(2)
                id_banco = candidato_original.get("_id_banco") if candidato_original else None
                if id_banco:
                    if col_ap.button("✅ Aprovar", key=f"aprovar_{id_banco}"):
                        db.registrar_decisao(id_banco, "Aprovado manualmente")
                        st.success("Decisão registrada. O sistema usará isso para aprender.")
                    if col_rep.button("❌ Reprovar", key=f"reprovar_{id_banco}"):
                        db.registrar_decisao(id_banco, "Reprovado manualmente")
                        st.success("Decisão registrada. O sistema usará isso para aprender.")

    secao_exportacao(df_filtrado[colunas_exibicao])


def secao_exportacao(df: pd.DataFrame):
    st.subheader("6. Exportação")
    col1, col2, col3 = st.columns(3)

    with col1:
        buffer_excel = io.BytesIO()
        with pd.ExcelWriter(buffer_excel, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Candidatos")
        st.download_button(
            "⬇️ Exportar Excel",
            data=buffer_excel.getvalue(),
            file_name="candidatos_analisados.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    with col2:
        csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "⬇️ Exportar CSV",
            data=csv_bytes,
            file_name="candidatos_analisados.csv",
            mime="text/csv",
        )

    with col3:
        pdf_bytes = gerar_pdf_resultados(df)
        st.download_button(
            "⬇️ Exportar PDF",
            data=pdf_bytes,
            file_name="candidatos_analisados.pdf",
            mime="application/pdf",
        )


def gerar_pdf_resultados(df: pd.DataFrame) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    from reportlab.lib.units import cm

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))
    colunas = ["Ranking", "Nome", "Pontuação", "Status", "Cidade", "Experiência"]
    colunas_presentes = [c for c in colunas if c in df.columns]
    dados_tabela = [colunas_presentes] + df[colunas_presentes].astype(str).values.tolist()

    tabela = Table(dados_tabela, repeatRows=1)
    tabela.setStyle(TableStyle([
       ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
("ALIGN", (0, 0), (-1, -1), "CENTER"),
("BOTTOMPADDING", (0, 0), (-1, 0), 8),
("TOPPADDING", (0, 1), (-1, -1), 5),
("BOTTOMPADDING", (0, 1), (-1, -1), 5),

    doc.build([tabela])
    return buffer.getvalue()


def secao_dashboard(df: pd.DataFrame):
    st.header("7. Dashboard")
    if df.empty:
        st.info("Analise currículos para ver o dashboard.")
        return

    col1, col2, col3 = st.columns(3)
    col1.metric("Total de candidatos", len(df))
    col2.metric("Pontuação média", f"{df['Pontuação'].mean():.1f}")
    col3.metric("Aprovados", int((df["Status"] == "Aprovado").sum()))

    col_a, col_b = st.columns(2)
    with col_a:
        fig_status = px.pie(df, names="Status", title="Distribuição por Status", hole=0.4)
        st.plotly_chart(fig_status, use_container_width=True)
    with col_b:
        fig_score = px.histogram(df, x="Pontuação", nbins=10, title="Distribuição de Notas")
        st.plotly_chart(fig_score, use_container_width=True)

    todas_competencias = []
    for resultado in st.session_state.resultados:
        todas_competencias.extend(resultado.get("competencias_encontradas", []))
    if todas_competencias:
        serie_competencias = pd.Series(todas_competencias).value_counts().head(10)
        fig_comp = px.bar(
            x=serie_competencias.values, y=serie_competencias.index, orientation="h",
            title="Competências mais encontradas", labels={"x": "Ocorrências", "y": "Competência"}
        )
        st.plotly_chart(fig_comp, use_container_width=True)

    col_c, col_d = st.columns(2)
    with col_c:
        fig_cidade = px.bar(df["Cidade"].value_counts(), title="Candidatos por Cidade")
        st.plotly_chart(fig_cidade, use_container_width=True)
    with col_d:
        fig_formacao = px.bar(df["Formação"].value_counts(), title="Candidatos por Formação")
        st.plotly_chart(fig_formacao, use_container_width=True)


def main():
    secao_cabecalho()
    arquivos, descricao_vaga, pesos_obrigatorias, pesos_desejaveis = secao_entrada()

    st.header("3. Análise")
    if st.button("🔎 Analisar", type="primary"):
        processar_analise(arquivos, descricao_vaga, pesos_obrigatorias, pesos_desejaveis)

    if not st.session_state.df_resultados.empty:
        df_filtrado = secao_filtros(st.session_state.df_resultados)
        secao_resultados(df_filtrado)
        secao_dashboard(df_filtrado)


if __name__ == "__main__":
    main()
