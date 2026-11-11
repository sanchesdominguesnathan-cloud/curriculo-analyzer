"""
app.py
Ponto de entrada da aplicação Streamlit — Análise Automática de Currículos.

Execução local:
    streamlit run app.py
"""

import io
import os

import streamlit as st
import pandas as pd
import plotly.express as px

# Correção #14: import de reportlab no topo do arquivo, junto com as
# demais dependências. O import tardio dentro de gerar_pdf_resultados
# estava escondendo a dependência sem nenhum benefício real.
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
from reportlab.lib.units import cm

import database as db
import auth
import parser as curriculo_parser
import score as score_module
import ranking
import forms_import
from keywords import (
    listar_todas_palavras_chave_sugeridas,
    montar_lista_palavras,
    BASE_SINONIMOS,
    PESOS_PADRAO,
)
from utils import gerar_hash_arquivo, gerar_resumo_candidato

st.set_page_config(page_title="Análise Automática de Currículos", layout="wide")

@st.cache_resource
def _inicializar_banco_uma_vez():
    """O Streamlit reexecuta este arquivo a CADA clique. Rodar o DDL
    (CREATE/ALTER/INDEX) toda vez custa conexões e pega travas de tabela;
    com cache_resource ele roda uma vez por processo (e não é cacheado se
    levantar exceção, então a próxima tentativa refaz)."""
    db.inicializar_banco()
    return True


try:
    _inicializar_banco_uma_vez()
except Exception as e:
    st.error(
        "⚠️ Não foi possível conectar ao banco de dados. Verifique se a "
        "variável `DATABASE_URL` está configurada corretamente nos Secrets "
        "do Streamlit Cloud (Manage app → Settings → Secrets)."
    )
    st.caption(f"Detalhe técnico: {e}")
    st.stop()


# ---------------------------------------------------------------------------
# Correção #10: inicialização do session_state em função dedicada,
# chamada no início de main(). Antes ficava no nível de módulo, misturado
# com código de setup global.
# ---------------------------------------------------------------------------
def _init_session():
    defaults = {
        "resultados": [],
        "df_resultados": pd.DataFrame(),
        "vaga_id_ativa": None,
        "ultima_vaga_carregada": None,
        "empresa_logada": None,
        # Correção #11: contador de tentativas de login para bloquear brute force.
        "tentativas_login": 0,
        # Correção #19: cidade da vaga ativa, usada para recalcular a
        # tabela de resultados quando uma decisão manual é registrada.
        "cidade_vaga_atual": "",
    }
    for chave, valor in defaults.items():
        if chave not in st.session_state:
            st.session_state[chave] = valor


def _obter_codigo_cadastro():
    """Código de acesso exigido para criar uma nova conta de empresa."""
    valor = os.environ.get("CODIGO_CADASTRO")
    if valor:
        return valor
    try:
        return st.secrets.get("CODIGO_CADASTRO", None)
    except Exception:
        return None


def tela_login():
    """Tela de login/cadastro exibida antes de qualquer acesso ao sistema."""
    st.title(" Análise Automática de Currículos")
    st.caption("Acesso restrito. Faça login para continuar.")

    aba_login, aba_cadastro = st.tabs([" Entrar", "🆕 Criar conta"])

    with aba_login:
        # Correção #11: bloqueia após 5 tentativas consecutivas falhas.
        MAX_TENTATIVAS = 5
        if st.session_state.tentativas_login >= MAX_TENTATIVAS:
            st.error(
                f"⛔ Muitas tentativas de login ({MAX_TENTATIVAS}). "
                "Recarregue a página para tentar novamente."
            )
            if st.button("🔄 Recarregar"):
                st.session_state.tentativas_login = 0
                st.rerun()
            return False

        with st.form("form_login"):
            email = st.text_input("E-mail")
            senha = st.text_input("Senha", type="password")
            enviar = st.form_submit_button("Entrar", type="primary")

        if enviar:
            empresa = db.autenticar_empresa(email, senha)
            if empresa:
                st.session_state.empresa_logada = empresa
                st.session_state.tentativas_login = 0
                st.rerun()
            else:
                st.session_state.tentativas_login += 1
                restantes = MAX_TENTATIVAS - st.session_state.tentativas_login
                if restantes > 0:
                    st.error(f"E-mail ou senha incorretos. {restantes} tentativa(s) restante(s).")
                else:
                    st.error("Conta bloqueada temporariamente. Recarregue a página.")

    with aba_cadastro:
        codigo_esperado = _obter_codigo_cadastro()
        with st.form("form_cadastro"):
            nome_empresa = st.text_input("Nome da empresa")
            email_cadastro = st.text_input("E-mail (será seu login)")
            senha_cadastro = st.text_input("Senha (mín. 8 caracteres, com maiúscula, número e caractere especial)", type="password")
            senha_confirmacao = st.text_input("Confirme a senha", type="password")
            codigo_digitado = None
            if codigo_esperado:
                codigo_digitado = st.text_input("Código de acesso (fornecido por quem vendeu a ferramenta)")
            criar = st.form_submit_button("Criar conta")

        if criar:
            if codigo_esperado and codigo_digitado != codigo_esperado:
                st.error("Código de acesso inválido.")
            elif not nome_empresa.strip() or not email_cadastro.strip():
                st.error("Preencha o nome da empresa e o e-mail.")
            elif senha_cadastro != senha_confirmacao:
                st.error("As senhas não coincidem.")
            else:
                senha_ok, motivo = auth.senha_e_forte(senha_cadastro)
                if not senha_ok:
                    st.error(motivo)
                else:
                    try:
                        db.criar_empresa(nome_empresa.strip(), email_cadastro.strip(), senha_cadastro)
                        st.success("Conta criada! Vá para a aba 'Entrar' e faça login.")
                    except ValueError as e:
                        st.error(str(e))

    return False


def secao_vaga(empresa_id: int):
    st.header(" Pasta da Vaga")

    vagas_salvas = db.listar_vagas(empresa_id)
    nomes_vagas = [v["nome"] for v in vagas_salvas]
    opcoes = ["➕ Criar nova vaga"] + nomes_vagas

    col_sel, col_del = st.columns([4, 1])
    with col_sel:
        escolha = st.selectbox(
            "Selecione uma vaga já salva ou crie uma nova",
            opcoes,
            key="seletor_vaga",
        )

    if escolha == "➕ Criar nova vaga":
        nome_vaga = st.text_input(
            "Nome desta vaga (ex: 'Analista Financeiro - Julho/2026')",
            key="nome_nova_vaga",
        )
        vaga_ativa = {
            "id": None, "nome": nome_vaga, "descricao": "",
            "obrigatorias": {}, "desejaveis": {}, "cidade_vaga": "",
        }
        st.session_state.vaga_id_ativa = None
        chave_vaga_atual = "NOVA_VAGA"
    else:
        vaga_selecionada = next(v for v in vagas_salvas if v["nome"] == escolha)
        vaga_ativa = db.obter_vaga(empresa_id, vaga_selecionada["id"])
        vaga_ativa["nome"] = escolha
        st.session_state.vaga_id_ativa = vaga_ativa["id"]
        chave_vaga_atual = vaga_ativa["id"]

        with col_del:
            st.write("")
            st.write("")
            if st.button("🗑️ Excluir vaga", key="excluir_vaga_btn"):
                db.excluir_vaga(empresa_id, vaga_ativa["id"])
                st.session_state.vaga_id_ativa = None
                st.session_state.ultima_vaga_carregada = None
                st.session_state.resultados = []
                st.session_state.df_resultados = pd.DataFrame()
                st.success(f"Vaga '{escolha}' excluída. Os currículos continuam salvos no histórico geral.")
                st.rerun()

    if st.session_state.ultima_vaga_carregada != chave_vaga_atual:
        if vaga_ativa["id"] is not None:
            candidatos_salvos = db.listar_curriculos_por_vaga(empresa_id, vaga_ativa["id"])
        else:
            candidatos_salvos = []

        st.session_state.resultados = candidatos_salvos
        if candidatos_salvos:
            st.session_state.df_resultados = ranking.montar_tabela_resultados(
                candidatos_salvos, cidade_vaga=vaga_ativa.get("cidade_vaga", "")
            )
            st.info(
                f"📂 Vaga '{escolha}' carregada com {len(candidatos_salvos)} "
                "candidato(s) já analisado(s) anteriormente. Envie novos "
                "currículos e clique em Analisar para adicionar mais."
            )
        else:
            st.session_state.df_resultados = pd.DataFrame()

        st.session_state.ultima_vaga_carregada = chave_vaga_atual

    # Correção #19: guarda a cidade da vaga assim que ela é carregada (não
    # só depois de rodar uma análise), para que a atualização da decisão
    # manual (Aprovar/Talvez/Reprovar) consiga recalcular a tabela mesmo
    # se o usuário só reabriu uma vaga já salva, sem enviar currículos novos.
    st.session_state.cidade_vaga_atual = vaga_ativa.get("cidade_vaga", "")

    return vaga_ativa

def secao_importar_forms(empresa_id: int):
    sheet_id = forms_import.SHEET_IDS_POR_EMPRESA.get(empresa_id)
    if not sheet_id:
        return  # essa empresa ainda não tem um Forms configurado

    with st.expander("📥 Importar candidaturas recebidas por e-mail (Google Forms)"):
        st.caption(
            "Candidatos que mandaram o currículo colado no corpo do e-mail "
            "podem ser cadastrados manualmente nesse formulário pelo "
            "recrutador. Clique abaixo pra trazer as candidaturas novas."
        )
        if st.button("🔄 Importar candidaturas novas", key="importar_forms_btn"):
            with st.spinner("Lendo a planilha e processando os currículos..."):
                try:
                    resumo = forms_import.importar_candidaturas_forms(empresa_id, sheet_id)
                except Exception as e:
                    st.error(f"Erro ao importar do Forms: {e}")
                    return

            if resumo["importados"] > 0:
                st.success(f"✅ {resumo['importados']} candidato(s) novo(s) importado(s).")
                st.session_state.ultima_vaga_carregada = None
                st.rerun()
            else:
                st.info("Nenhuma candidatura nova encontrada na planilha.")

            if resumo["ignorados_sem_texto"] > 0:
                st.warning(f"⚠️ {resumo['ignorados_sem_texto']} linha(s) ignorada(s) por não ter currículo preenchido.")

            if resumo["vagas_nao_encontradas"]:
                st.warning("⚠️ Candidatos com vaga não reconhecida:")
                for item in resumo["vagas_nao_encontradas"]:
                    st.caption(f"- {item['candidato']} → vaga digitada: \"{item['vaga_digitada']}\"")

def secao_cabecalho():
    st.title(" Análise Automática de Currículos")
    st.caption(
        "Faça upload dos currículos, descreva a vaga e cadastre as "
        "palavras-chave. O sistema extrai os dados, pontua e classifica "
        "os candidatos automaticamente."
    )


def secao_entrada(empresa_id: int, vaga_ativa: dict):
    st.header("1. Vaga e Currículos")

    sufixo_widget = f"{vaga_ativa.get('id') or 'nova'}_{vaga_ativa.get('nome', '')}"

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
            value=vaga_ativa.get("descricao", ""),
            placeholder="Ex: Buscamos um Analista Administrativo com experiência em "
                        "gestão de processos, domínio de Excel e Power BI...",
            height=150,
            key=f"descricao_vaga_{sufixo_widget}",
        )
        cidade_vaga = st.text_input(
            " Cidade da vaga (opcional)",
            value=vaga_ativa.get("cidade_vaga", ""),
            placeholder="Ex: São Paulo",
            help="Se preenchido, candidatos de outra cidade (ou sem cidade identificada "
                 "no currículo) recebem um alerta na tabela de resultados.",
            key=f"cidade_vaga_{sufixo_widget}",
        )

    st.subheader("2. Palavras-chave")
    st.caption(
        "Digite as palavras-chave separadas por vírgula. Você pode ajustar o "
        "peso de cada uma na tabela abaixo. Sinônimos (ex: 'Planilhas' para "
        "'Excel') são reconhecidos automaticamente."
    )

    sugestoes = ", ".join(listar_todas_palavras_chave_sugeridas())
    obrigatorias_padrao = ", ".join(vaga_ativa.get("obrigatorias", {}).keys())
    desejaveis_padrao = ", ".join(vaga_ativa.get("desejaveis", {}).keys())

    col_obg, col_desej = st.columns(2)
    with col_obg:
        texto_obrigatorias = st.text_area(
            "🔴 Palavras-chave OBRIGATÓRIAS (separadas por vírgula)",
            value=obrigatorias_padrao,
            placeholder=sugestoes,
            height=100,
            key=f"input_obrigatorias_{sufixo_widget}",
        )
    with col_desej:
        texto_desejaveis = st.text_area(
            "🟡 Palavras-chave DESEJÁVEIS (separadas por vírgula)",
            value=desejaveis_padrao,
            placeholder="Ex: Inglês, Liderança, Comunicação",
            height=100,
            key=f"input_desejaveis_{sufixo_widget}",
        )

    # Correção #20: remove palavras-chave duplicadas (ignorando maiúsculas/
    # minúsculas) antes de gerar os sliders de peso. Sem isso, digitar a
    # mesma palavra duas vezes (ex: "Excel, Power BI, Excel") gerava dois
    # sliders com a MESMA key no Streamlit e travava a tela inteira com um
    # erro de "elemento duplicado". Também evita contar a mesma competência
    # duas vezes na pontuação (ex: "Excel" e "excel" juntos inflando o peso
    # desse critério sem o recrutador perceber).
    def _dedup_preservando_ordem(itens):
        vistos = set()
        resultado = []
        for item in itens:
            chave = item.lower()
            if chave not in vistos:
                vistos.add(chave)
                resultado.append(item)
        return resultado

    palavras_obrigatorias_lista = _dedup_preservando_ordem(
        p.strip() for p in texto_obrigatorias.split(",") if p.strip()
    )
    palavras_desejaveis_lista = _dedup_preservando_ordem(
        p.strip() for p in texto_desejaveis.split(",") if p.strip()
    )

    pesos_salvos_obrig = vaga_ativa.get("obrigatorias", {})
    pesos_salvos_desej = vaga_ativa.get("desejaveis", {})

    pesos_obrigatorias, pesos_desejaveis = {}, {}

    if palavras_obrigatorias_lista or palavras_desejaveis_lista:
        with st.expander("⚙️ Ajustar pesos das palavras-chave", expanded=False):
            st.caption("Peso de 1 a 10. Quanto maior o peso, mais essa palavra impacta a pontuação final.")
            if palavras_obrigatorias_lista:
                st.markdown("**Obrigatórias**")
                cols = st.columns(min(4, len(palavras_obrigatorias_lista)) or 1)
                for i, palavra in enumerate(palavras_obrigatorias_lista):
                    peso_padrao = pesos_salvos_obrig.get(palavra, PESOS_PADRAO.get(palavra.lower().strip(), 8))
                    with cols[i % len(cols)]:
                        pesos_obrigatorias[palavra] = st.slider(
                            palavra, 1, 10, peso_padrao, key=f"peso_obg_{sufixo_widget}_{palavra}"
                        )
            if palavras_desejaveis_lista:
                st.markdown("**Desejáveis**")
                cols = st.columns(min(4, len(palavras_desejaveis_lista)) or 1)
                for i, palavra in enumerate(palavras_desejaveis_lista):
                    peso_padrao = pesos_salvos_desej.get(palavra, PESOS_PADRAO.get(palavra.lower().strip(), 5))
                    with cols[i % len(cols)]:
                        pesos_desejaveis[palavra] = st.slider(
                            palavra, 1, 10, peso_padrao, key=f"peso_dej_{sufixo_widget}_{palavra}"
                        )
    else:
        pesos_obrigatorias = {}
        pesos_desejaveis = {}

    if not pesos_obrigatorias:
        pesos_obrigatorias = {
            p: pesos_salvos_obrig.get(p, PESOS_PADRAO.get(p.lower().strip(), 8)) for p in palavras_obrigatorias_lista
        }
    if not pesos_desejaveis:
        pesos_desejaveis = {
            p: pesos_salvos_desej.get(p, PESOS_PADRAO.get(p.lower().strip(), 5)) for p in palavras_desejaveis_lista
        }

    nome_para_salvar = vaga_ativa.get("nome") or ""
    if st.button("Salvar esta vaga (descrição, palavras-chave e cidade)", key=f"salvar_vaga_{sufixo_widget}"):
        if not nome_para_salvar.strip():
            st.error("Dê um nome para a vaga antes de salvar (campo acima do upload).")
        else:
            novo_id = db.salvar_vaga(
                empresa_id, nome_para_salvar.strip(), descricao_vaga, pesos_obrigatorias, pesos_desejaveis, cidade_vaga
            )
            st.session_state.vaga_id_ativa = novo_id
            st.success(f"Vaga '{nome_para_salvar}' salva! Você pode voltar nela quando quiser, em outro dia.")
            st.rerun()

    return arquivos, descricao_vaga, cidade_vaga, pesos_obrigatorias, pesos_desejaveis


def processar_analise(empresa_id, arquivos, descricao_vaga, cidade_vaga, pesos_obrigatorias, pesos_desejaveis, vaga_id=None):
    if not arquivos:
        st.warning("Envie ao menos um currículo antes de analisar.")
        return

    # Ordem determinística e sem repetição (antes: list(set(...)), cuja ordem
    # muda a cada reinício e embaralhava as "Principais competências").
    todas_palavras = montar_lista_palavras(pesos_obrigatorias, pesos_desejaveis)
    if not todas_palavras:
        todas_palavras = listar_todas_palavras_chave_sugeridas()

    # Correção #20: avisa a recrutadora se ela esqueceu de definir
    # palavras-chave — sem isso, o score.py agora usa só experiência,
    # formação e idiomas pra pontuar (em vez de inflar a nota de todo
    # mundo pra 100% "de brinde" nesse critério, como acontecia antes).
    if not pesos_obrigatorias and not pesos_desejaveis:
        st.warning(
            "⚠️ Nenhuma palavra-chave obrigatória ou desejável foi definida "
            "pra essa vaga. A pontuação será calculada só com base em "
            "experiência, formação e idiomas — considere preencher as "
            "palavras-chave na seção 2 pra um resultado mais preciso."
        )

    novos_resultados = []
    barra = st.progress(0, text="Iniciando análise...")

    hashes_vistos = {}  # hash -> nome do 1º arquivo com esse conteúdo, neste lote

    for i, arquivo in enumerate(arquivos):
        barra.progress((i) / len(arquivos), text=f"Analisando {arquivo.name}...")

        # Correção (candidatos "pulando"): TODO o corpo do loop fica dentro de
        # um try. Antes só ValueError era tratado; um PDF corrompido levanta
        # PdfminerException e um .docx inválido levanta BadZipFile, que
        # derrubavam a análise INTEIRA — os arquivos seguintes do lote nunca
        # eram processados nem salvos, e a tela ficava com resultado parcial.
        try:
            conteudo_bytes = arquivo.getvalue()
            hash_arquivo = gerar_hash_arquivo(conteudo_bytes)

            # Arquivo idêntico enviado duas vezes no mesmo lote (mesmo
            # conteúdo, nomes diferentes): processa uma vez e avisa, em vez
            # de sobrescrever em silêncio o primeiro pelo segundo.
            if hash_arquivo in hashes_vistos:
                st.warning(
                    f"🔁 '{arquivo.name}' tem exatamente o mesmo conteúdo de "
                    f"'{hashes_vistos[hash_arquivo]}' e foi ignorado."
                )
                continue
            hashes_vistos[hash_arquivo] = arquivo.name

            ja_analisado = db.curriculo_ja_analisado(empresa_id, hash_arquivo, vaga_id)

            # Correção #20 (mantida): a pontuação é SEMPRE recalculada com os
            # pesos atuais. Correção nova: o currículo também é re-extraído
            # POR COMPLETO (nome, experiência, competências, formação...).
            # Antes, num reenvio, o código reaproveitava os dados antigos do
            # banco (`dict(ja_analisado)`): um nome extraído errado ficava
            # preso para sempre mesmo depois de corrigir o parser, e o resumo
            # era regerado em cima de competências/experiência velhas — por
            # isso o "resumo profissional" aparecia desatualizado ou trocado.
            try:
                dados = curriculo_parser.processar_curriculo(arquivo.name, conteudo_bytes, todas_palavras)
            except Exception as e:
                if ja_analisado:
                    st.info(
                        f"⏭️ '{arquivo.name}' já havia sido analisado, mas não foi "
                        f"possível reler o conteúdo agora ({e}) — mantendo a "
                        "pontuação salva anteriormente."
                    )
                    ja_analisado["_bytes_download"] = conteudo_bytes
                    novos_resultados.append(ja_analisado)
                else:
                    st.error(f"❌ Não foi possível analisar '{arquivo.name}': {e}")
                continue

            ajuste = db.calcular_ajuste_aprendizado(empresa_id, dados.get("competencias_encontradas", []))
            resultado_score = score_module.calcular_score_final(
                dados, pesos_obrigatorias, pesos_desejaveis, ajuste_aprendizado=ajuste
            )
            dados.update(resultado_score)
            dados["status_automatico"] = resultado_score["status"]
            dados["resumo"] = gerar_resumo_candidato(dados)
            dados["hash_arquivo"] = hash_arquivo

            # Correção #19: a decisão manual do recrutador continua valendo
            # depois de recalcular a pontuação automática.
            if ja_analisado and ja_analisado.get("decisao_manual"):
                dados["status"] = ja_analisado["decisao_manual"]
                dados["decisao_manual"] = ja_analisado["decisao_manual"]

            # Bytes só para o botão de download desta sessão — nunca vão ao banco.
            dados["_bytes_download"] = conteudo_bytes

            # Remove o texto bruto ANTES de salvar (defesa em profundidade;
            # o database.py também filtra esse campo).
            dados.pop("_texto_bruto_temp", None)

            id_banco = db.salvar_curriculo(empresa_id, hash_arquivo, arquivo.name, dados, descricao_vaga, vaga_id=vaga_id)
            dados["_id_banco"] = id_banco

            novos_resultados.append(dados)
        except Exception as e:  # noqa: BLE001 — um arquivo ruim não pode parar o lote
            st.error(f"❌ Falha inesperada ao processar '{arquivo.name}': {e}")
            continue

    barra.progress(1.0, text="Análise concluída!")

    resultados_por_hash = {r.get("hash_arquivo"): r for r in st.session_state.resultados if r.get("hash_arquivo")}
    for r in novos_resultados:
        resultados_por_hash[r.get("hash_arquivo")] = r
    resultados_final = list(resultados_por_hash.values())

    st.session_state.resultados = resultados_final
    st.session_state.cidade_vaga_atual = cidade_vaga
    st.session_state.df_resultados = ranking.montar_tabela_resultados(resultados_final, cidade_vaga=cidade_vaga)


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
        cargo = st.text_input("Cargo/competência")

    somente_mesma_cidade = False
    if "Fora da cidade da vaga" in df.columns and df["Fora da cidade da vaga"].any():
        somente_mesma_cidade = st.checkbox(
            "📍 Mostrar apenas candidatos da cidade da vaga (ocultar quem tem alerta)"
        )

    df_filtrado = ranking.aplicar_filtros(
        df,
        cidade=cidade or None,
        experiencia_min_meses=experiencia_min or None,
        idioma=idioma or None,
        formacao=formacao or None,
        pontuacao_minima=pontuacao_min or None,
        cargo=cargo or None,
    )

    if somente_mesma_cidade and "Fora da cidade da vaga" in df_filtrado.columns:
        df_filtrado = df_filtrado[~df_filtrado["Fora da cidade da vaga"]].reset_index(drop=True)

    return df_filtrado


def cor_status(status):
    return {
        "Aprovado": "background-color: #1e5631; color: #ffffff",
        "Talvez": "background-color: #7a5c00; color: #ffffff",
        "Reprovado": "background-color: #7a1f1f; color: #ffffff",
    }.get(status, "")


def texto_status_com_icone(status):
    icones = {"Aprovado": "✅ Aprovado", "Talvez": "⚠️ Talvez", "Reprovado": "❌ Reprovado"}
    return icones.get(status, status)


def _registrar_decisao_manual(id_banco: int, decisao: str, candidato_original: dict):
    """Registra a decisão do recrutador no banco e atualiza o session_state
    na hora, para que a tabela e o dashboard reflitam a mudança imediatamente.

    Correção #19: antes, db.registrar_decisao só gravava na tabela
    `decisoes` (usada pelo sistema de aprendizado) e a função parava por
    aí — o candidato em si nunca era atualizado, então clicar em
    Aprovar/Talvez/Reprovar não tinha nenhum efeito visível na tela. Além
    de já termos corrigido isso no lado do banco (coluna
    `decisao_manual` em `curriculos`), aqui também atualizamos o
    session_state e recalculamos `df_resultados` na hora, em vez de
    depender de o usuário recarregar a vaga manualmente para ver o
    resultado."""
    empresa_logada = st.session_state.get("empresa_logada") or {}
    try:
        db.registrar_decisao(empresa_logada.get("id"), id_banco, decisao)
    except (PermissionError, ValueError) as e:
        st.error(f"Não foi possível registrar a decisão: {e}")
        return

    if candidato_original is not None:
        candidato_original["status"] = decisao
        candidato_original["decisao_manual"] = decisao

    st.session_state.df_resultados = ranking.montar_tabela_resultados(
        st.session_state.resultados,
        cidade_vaga=st.session_state.get("cidade_vaga_atual", ""),
    )
    st.success(f"Decisão '{decisao}' registrada — a tabela e o dashboard já foram atualizados.")
    st.rerun()

def secao_resultados(df_filtrado: pd.DataFrame):
    st.header("5. Resultados")

    if df_filtrado.empty:
        st.info("Nenhum resultado para exibir com os filtros atuais.")
        return

    tem_alerta_cidade = (
        "Alerta" in df_filtrado.columns
        and (df_filtrado["Alerta"] != "").any()
    )

    if tem_alerta_cidade:
        qtd_alertas = int((df_filtrado["Alerta"] != "").sum())
        st.warning(
            f"⚠️ {qtd_alertas} candidato(s) fora da cidade da vaga "
            "(ou sem cidade identificada). Veja a coluna 'Alerta' na tabela abaixo."
        )

    colunas_exibicao = [
        "Ranking",
        "Nome",
        "Pontuação",
        "Compatibilidade (%)",
        "Experiência",
        "Formação",
        "Idiomas",
        "Cidade",
        "Alerta",
        "Duplicidade",
        "Status",
    ]

    colunas_exibicao = [
        c for c in colunas_exibicao if c in df_filtrado.columns
    ]

    df_exibicao = df_filtrado[colunas_exibicao].copy()
    df_exibicao["Status"] = df_exibicao["Status"].apply(texto_status_com_icone)

    st.dataframe(
        df_exibicao.style.apply(
            lambda linha: [
                cor_status(
                    linha["Status"]
                    .replace("✅ ", "")
                    .replace("⚠️ ", "")
                    .replace("❌ ", "")
                )
            ] * len(linha),
            axis=1,
        ),
        use_container_width=True,
        height=420,
    )

    st.subheader("Detalhes e decisão por candidato")

    for _, linha in df_filtrado.iterrows():
        with st.expander(
            f"#{linha['Ranking']} — {linha['Nome']} "
            f"({linha['Status']}, {linha['Pontuação']} pts)"
        ):
            colA, colB = st.columns([2, 1])

            with colA:
                st.markdown(f"**Resumo:** {linha['Resumo']}")
                st.markdown(
                    f"**Email:** {linha['Email']} | "
                    f"**Telefone:** {linha['Telefone']}"
                )
                st.markdown(f"**LinkedIn:** {linha['LinkedIn']}")

            with colB:
                candidato_original = next(
                    (
                        r
                        for r in st.session_state.resultados
                        if r.get("hash_arquivo") == linha["Hash"]
                    ),
                    None,
                )

                bytes_download = (
                    candidato_original.get("_bytes_download")
                    if candidato_original
                    else None
                )

                if bytes_download:
                    st.download_button(
                        "📎 Abrir currículo",
                        data=bytes_download,
                        file_name=linha["Arquivo"],
                        key=f"download_{linha['Arquivo']}_{linha['Ranking']}",
                    )
                else:
                    st.caption(
                        "📎 Arquivo disponível apenas na sessão "
                        "em que foi enviado."
                    )

                id_banco = (
                    candidato_original.get("_id_banco")
                    if candidato_original
                    else None
                )

                if id_banco:
                    col_ap, col_talvez, col_rep = st.columns(3)

                    if col_ap.button(
                        "✅ Aprovar",
                        key=f"aprovar_{id_banco}",
                    ):
                        _registrar_decisao_manual(
                            id_banco,
                            "Aprovado",
                            candidato_original,
                        )

                    if col_talvez.button(
                        "🤔 Talvez",
                        key=f"talvez_{id_banco}",
                    ):
                        _registrar_decisao_manual(
                            id_banco,
                            "Talvez",
                            candidato_original,
                        )

                    if col_rep.button(
                        "❌ Reprovar",
                        key=f"reprovar_{id_banco}",
                    ):
                        _registrar_decisao_manual(
                            id_banco,
                            "Reprovado",
                            candidato_original,
                        )
                else:
                    st.caption(
                        "Decisão manual disponível após a análise "
                        "ser salva no histórico."
                    )

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
    # Correção #14: imports movidos para o topo do arquivo.
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))
    colunas = ["Ranking", "Nome", "Pontuação", "Status", "Cidade", "Alerta", "Experiência"]
    colunas_presentes = [c for c in colunas if c in df.columns]

    df_pdf = df[colunas_presentes].copy()
    if "Alerta" in df_pdf.columns:
        df_pdf["Alerta"] = df_pdf["Alerta"].str.replace("⚠️ ", "", regex=False)

    dados_tabela = [colunas_presentes] + df_pdf.astype(str).values.tolist()

    tabela = Table(dados_tabela, repeatRows=1)
    tabela.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]))

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
    # Correção #10: session_state inicializado em função dedicada.
    _init_session()

    if not st.session_state.empresa_logada:
        tela_login()
        return

    empresa = st.session_state.empresa_logada
    empresa_id = empresa["id"]
    secao_importar_forms(empresa_id)

    col_titulo, col_logout = st.columns([5, 1])
    with col_logout:
        st.write("")
        if st.button(f" Sair ({empresa['nome']})"):
            st.session_state.empresa_logada = None
            st.session_state.resultados = []
            st.session_state.df_resultados = pd.DataFrame()
            st.session_state.vaga_id_ativa = None
            st.session_state.ultima_vaga_carregada = None
            st.session_state.tentativas_login = 0
            st.rerun()

    secao_cabecalho()
    vaga_ativa = secao_vaga(empresa_id)
    arquivos, descricao_vaga, cidade_vaga, pesos_obrigatorias, pesos_desejaveis = secao_entrada(empresa_id, vaga_ativa)

    st.header("3. Análise")
    if st.button(" Analisar", type="primary"):
        nome_vaga_atual = (vaga_ativa.get("nome") or "").strip()

        # Correção #20: bloqueia a análise se a vaga ainda não tem nome.
        # Antes, isso era permitido silenciosamente — os currículos eram
        # analisados e mostrados na tela normalmente, mas salvos com
        # vaga_id nulo. Na próxima vez que o recrutador entrasse no
        # sistema, esses candidatos não apareciam em NENHUMA vaga da
        # lista, dando a impressão de que os dados tinham sumido.
        if vaga_ativa.get("id") is None and not nome_vaga_atual:
            st.error(
                "⚠️ Dê um nome pra essa vaga antes de analisar (campo no "
                "topo da seção '1. Vaga e Currículos'). Sem nome, os "
                "candidatos analisados não ficam vinculados a nenhuma "
                "vaga e não aparecem mais depois."
            )
        else:
            vaga_id_para_salvar = st.session_state.vaga_id_ativa
            if vaga_id_para_salvar is None and nome_vaga_atual:
                vaga_id_para_salvar = db.salvar_vaga(
                    empresa_id, nome_vaga_atual, descricao_vaga, pesos_obrigatorias, pesos_desejaveis, cidade_vaga
                )
                st.session_state.vaga_id_ativa = vaga_id_para_salvar

            processar_analise(
                empresa_id, arquivos, descricao_vaga, cidade_vaga, pesos_obrigatorias, pesos_desejaveis,
                vaga_id=vaga_id_para_salvar,
            )

    if not st.session_state.df_resultados.empty:
        df_filtrado = secao_filtros(st.session_state.df_resultados)
        secao_resultados(df_filtrado)
        secao_dashboard(df_filtrado)


if __name__ == "__main__":
    main()
