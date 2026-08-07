"""
forms_import.py
Importa candidaturas recebidas via Google Forms — candidatos que mandaram
o currículo colado no corpo do e-mail, em vez de anexar um PDF/DOCX, e que
por isso não conseguiam entrar no fluxo normal de upload.

Fluxo:
1. Lê as respostas da planilha vinculada ao Google Form via gspread,
   autenticado com uma service account do Google Cloud (sem custo de API).
2. Casa o nome da vaga escolhida no dropdown do Forms com o `vaga_id` já
   cadastrado no banco daquela empresa (precisa ser texto idêntico).
3. Roda o texto colado pelas MESMAS funções de análise do parser.py
   (formação, idiomas, competências, experiência) — só pula a etapa de
   extração de PDF/DOCX, porque o texto já vem pronto.
4. Calcula o score com score.py (idêntico ao caminho de upload normal) e
   salva com database.salvar_curriculo, reaproveitando a deduplicação por
   hash que já existe lá (evita reimportar o mesmo candidato duas vezes).

Configuração necessária:
- Variável de ambiente GOOGLE_SERVICE_ACCOUNT_JSON no Render, com o
  conteúdo completo do arquivo JSON da service account (não um caminho de
  arquivo — o texto inteiro do JSON).
- A planilha de respostas do Forms precisa estar compartilhada (permissão
  de Leitor) com o email dessa service account.
- Dependências novas no requirements.txt: gspread, google-auth
"""

import os
import json

import gspread
from google.oauth2.service_account import Credentials

import database as db
import score as score_module
from parser import (
    extrair_formacao,
    extrair_idiomas,
    extrair_competencias,
    calcular_experiencia_total,
    extrair_linkedin,
    extrair_email,
    extrair_telefone,
    extrair_cidade,
)
from utils import gerar_hash_arquivo, gerar_resumo_candidato, normalizar_texto
from keywords import listar_todas_palavras_chave_sugeridas


# ---------------------------------------------------------------------------
# Configuração: qual planilha pertence a qual empresa
# ---------------------------------------------------------------------------
# Cada empresa cliente tem seu próprio Google Form (e portanto sua própria
# planilha de respostas) — isso garante isolamento entre empresas mesmo que
# um recrutador erre algum campo do formulário. Atualize esse dicionário
# manualmente sempre que criar um Form novo pra uma empresa nova.
#
# Como pegar o sheet_id: é o trecho da URL entre "/d/" e "/edit".
SHEET_IDS_POR_EMPRESA = {
    1: "11PTdjVSPDFwseWn1_Pdck1f9eZSma01bGBxzqZ4mcOc", #Teste da entrada do forms
    2: "11NuyNHEbFeW7BuTXjOdyAWGfvRxQLym4gSZSvMShoG8/edit", #Gabriela 
}


ESCOPOS = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Nomes exatos das colunas geradas pelo Google Forms — precisam bater com
# os títulos das perguntas cadastradas no Forms. Se você renomear uma
# pergunta no Forms, atualize aqui também.
COLUNA_TIMESTAMP = "Carimbo de data/hora"
COLUNA_NOME = "Nome completo do candidato"
COLUNA_EMAIL = "E-mail do candidato"
COLUNA_TELEFONE = "Telefone"
COLUNA_VAGA = "Vaga"
COLUNA_CIDADE = "Cidade do candidato"
COLUNA_TEXTO = "Cole aqui o texto do currículo"


def _obter_cliente_gspread():
    """Autentica com a service account a partir da variável de ambiente
    GOOGLE_SERVICE_ACCOUNT_JSON (conteúdo do JSON, não um caminho de arquivo)."""
    credenciais_texto = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not credenciais_texto:
        raise RuntimeError(
            "Variável GOOGLE_SERVICE_ACCOUNT_JSON não configurada. Configure "
            "nas Environment Variables do Render com o conteúdo completo do "
            "arquivo JSON da service account."
        )
    info = json.loads(credenciais_texto)
    credenciais = Credentials.from_service_account_info(info, scopes=ESCOPOS)
    return gspread.authorize(credenciais)


def _ler_linhas_planilha(sheet_id: str) -> list:
    cliente = _obter_cliente_gspread()
    planilha = cliente.open_by_key(sheet_id)
    aba = planilha.sheet1
    # numericise_ignore=['all'] faz o gspread trazer TODAS as células como
    # string, sem tentar adivinhar se o conteúdo "parece" um número. Isso
    # evita que colunas como Telefone/CEP virem int/float automaticamente
    # e resolve na raiz o erro "'int' object has no attribute 'strip'".
    return aba.get_all_records(numericise_ignore=["all"])


def _texto(valor) -> str:
    """Converte qualquer valor vindo da planilha (str, int, float, bool,
    None) em texto limpo, com espaços nas pontas removidos.

    Necessário como segunda camada de proteção: mesmo usando
    numericise_ignore no gspread, é mais seguro que qualquer ponto do
    código que leia uma célula da planilha nunca quebre se um dia vier um
    tipo inesperado (int, float, None etc.) — por exemplo, se uma nova
    pergunta numérica for adicionada ao Forms no futuro."""
    if valor is None:
        return ""
    return str(valor).strip()


def _normalizar_linha(linha: dict) -> dict:
    """Normaliza as chaves (nomes das colunas) do dicionário — remove
    acentos, deixa minúsculo e limpa espaços extras — antes de qualquer
    leitura.

    Corrige um bug real: a mesma palavra acentuada (ex: 'currículo') pode
    ser salva com uma codificação Unicode ligeiramente diferente dependendo
    de onde foi digitada/copiada, e aí uma comparação exata de string falha
    silenciosamente mesmo com os textos parecendo idênticos na tela. Usando
    normalizar_texto (já existente em utils.py) dos dois lados da
    comparação, isso deixa de importar."""
    return {normalizar_texto(k): v for k, v in linha.items()}


def _valor(linha_normalizada: dict, nome_coluna: str):
    """Busca o valor de uma coluna na linha já normalizada, normalizando
    também o nome da coluna que estamos procurando."""
    return linha_normalizada.get(normalizar_texto(nome_coluna))


def _casar_vaga(empresa_id: int, nome_vaga_digitado: str):
    """Encontra a vaga cadastrada da empresa cujo nome é idêntico ao
    selecionado no dropdown do Forms. Retorna (vaga_id, vaga_dict) ou
    (None, None) se não encontrar."""
    if not nome_vaga_digitado:
        return None, None
    alvo_normalizado = normalizar_texto(nome_vaga_digitado)
    vagas = db.listar_vagas(empresa_id)
    for v in vagas:
        if normalizar_texto(v["nome"]) == alvo_normalizado:
            vaga_completa = db.obter_vaga(empresa_id, v["id"])
            return v["id"], vaga_completa
    return None, None


def _montar_dados_candidato(linha_normalizada: dict, vaga: dict):
    """Monta o dicionário 'dados' no mesmo formato que
    parser.processar_curriculo retorna, mas a partir do texto colado no
    Forms em vez de um arquivo PDF/DOCX."""
    texto = _texto(_valor(linha_normalizada, COLUNA_TEXTO))

    palavras_obrigatorias = vaga.get("obrigatorias", {}) if vaga else {}
    palavras_desejaveis = vaga.get("desejaveis", {}) if vaga else {}
    todas_palavras = list(set(list(palavras_obrigatorias.keys()) + list(palavras_desejaveis.keys())))
    if not todas_palavras:
        todas_palavras = listar_todas_palavras_chave_sugeridas()

    nivel_formacao, area_formacao = extrair_formacao(texto)
    idiomas = extrair_idiomas(texto)
    competencias, evidencias = extrair_competencias(texto, todas_palavras)
    total_meses, experiencia_formatada = calcular_experiencia_total(texto)

    nome_candidato = _texto(_valor(linha_normalizada, COLUNA_NOME)) or "Não identificado"
    nome_arquivo = f"[Forms] {nome_candidato}"

    dados = {
        "arquivo": nome_arquivo,
        # Campos vindos direto do formulário (mais confiáveis que regex,
        # já que o próprio recrutador digitou) — cai pro texto só se vier vazio.
        "nome": nome_candidato,
        "email": _texto(_valor(linha_normalizada, COLUNA_EMAIL)) or extrair_email(texto),
        "telefone": _texto(_valor(linha_normalizada, COLUNA_TELEFONE)) or extrair_telefone(texto),
        "cidade": _texto(_valor(linha_normalizada, COLUNA_CIDADE)) or extrair_cidade(texto),
        "linkedin": extrair_linkedin(texto),
        "formacao_nivel": nivel_formacao,
        "formacao_area": area_formacao,
        "idiomas": idiomas,
        "competencias_encontradas": competencias,
        "evidencias_competencias": evidencias,
        "experiencia_meses": total_meses,
        "experiencia_formatada": experiencia_formatada,
        "_texto_bruto_temp": texto,
        "_origem": "google_forms",
    }
    return dados, nome_arquivo, palavras_obrigatorias, palavras_desejaveis


def importar_candidaturas_forms(empresa_id: int, sheet_id: str) -> dict:
    """Importa todas as linhas novas da planilha de respostas do Forms
    pra essa empresa. Retorna um resumo com contagens, pra exibir feedback
    na tela."""
    linhas = _ler_linhas_planilha(sheet_id)

    importados = 0
    ignorados_sem_texto = 0
    vagas_nao_encontradas = []

    for linha_bruta in linhas:
        linha = _normalizar_linha(linha_bruta)

        texto = _texto(_valor(linha, COLUNA_TEXTO))
        if len(texto) < 20:
            ignorados_sem_texto += 1
            continue

        nome_vaga_digitado = _texto(_valor(linha, COLUNA_VAGA))
        vaga_id, vaga = _casar_vaga(empresa_id, nome_vaga_digitado)
        if vaga_id is None:
            vagas_nao_encontradas.append({
                "candidato": _valor(linha, COLUNA_NOME) or "?",
                "vaga_digitada": nome_vaga_digitado,
            })
            continue

        dados, nome_arquivo, pesos_obrig, pesos_desej = _montar_dados_candidato(linha, vaga)

        # Hash de deduplicação: como não existe arquivo físico pra gerar
        # hash de bytes (como no upload normal), usamos o timestamp da
        # submissão + email como identificador único e estável da linha.
        chave_dedup = f"{_texto(_valor(linha, COLUNA_TIMESTAMP))}|{dados.get('email', '')}"
        hash_arquivo = gerar_hash_arquivo(chave_dedup.encode("utf-8"))

        ja_existe = db.curriculo_ja_analisado(empresa_id, hash_arquivo)
        if ja_existe:
            continue  # já importado numa sincronização anterior

        ajuste = db.calcular_ajuste_aprendizado(empresa_id, dados.get("competencias_encontradas", []))
        resultado_score = score_module.calcular_score_final(
            dados, pesos_obrig, pesos_desej, ajuste_aprendizado=ajuste
        )
        dados.update(resultado_score)
        dados["resumo"] = gerar_resumo_candidato(dados)
        dados["hash_arquivo"] = hash_arquivo
        dados.pop("_texto_bruto_temp", None)

        db.salvar_curriculo(
            empresa_id, hash_arquivo, nome_arquivo, dados,
            vaga_descricao=vaga.get("descricao", ""), vaga_id=vaga_id,
        )
        importados += 1

    return {
        "importados": importados,
        "ignorados_sem_texto": ignorados_sem_texto,
        "vagas_nao_encontradas": vagas_nao_encontradas,
    }
