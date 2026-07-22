"""
parser.py
Responsável por ler os arquivos de currículo (PDF e DOCX), extrair o texto
bruto e, a partir dele, extrair os campos estruturados: nome, telefone,
email, cidade, LinkedIn, experiência, formação, idiomas, competências, etc.
"""

import re
import io
import pdfplumber
import docx

from utils import (
    normalizar_texto,
    extrair_periodos,
    calcular_meses_experiencia,
    formatar_experiencia,
)
from keywords import encontrar_ocorrencias, listar_todas_palavras_chave_sugeridas

# ---------------------------------------------------------------------------
# Extração de texto bruto
# ---------------------------------------------------------------------------

def extrair_texto_pdf(arquivo_bytes: bytes) -> str:
    texto_completo = []
    with pdfplumber.open(io.BytesIO(arquivo_bytes)) as pdf:
        for pagina in pdf.pages:
            texto_pagina = pagina.extract_text() or ""
            texto_completo.append(texto_pagina)
    return "\n".join(texto_completo)


def extrair_texto_docx(arquivo_bytes: bytes) -> str:
    documento = docx.Document(io.BytesIO(arquivo_bytes))

    # CORREÇÃO: parágrafos primeiro — o nome quase sempre está fora da tabela.
    # Currículos com layout de duas colunas têm nome no parágrafo do topo;
    # misturar tudo sem ordem quebrava a detecção de nome.
    partes = [p.text for p in documento.paragraphs if p.text.strip()]

    for tabela in documento.tables:
        for linha in tabela.rows:
            for celula in linha.cells:
                texto_celula = celula.text.strip()
                if texto_celula:
                    partes.append(texto_celula)

    return "\n".join(partes)


def extrair_texto(nome_arquivo: str, arquivo_bytes: bytes) -> str:
    """Detecta o tipo do arquivo pela extensão e extrai o texto."""
    nome_lower = nome_arquivo.lower()
    if nome_lower.endswith(".pdf"):
        return extrair_texto_pdf(arquivo_bytes)
    elif nome_lower.endswith(".docx"):
        return extrair_texto_docx(arquivo_bytes)
    else:
        raise ValueError(f"Formato de arquivo não suportado: {nome_arquivo}")


# ---------------------------------------------------------------------------
# Extração de campos individuais via regex
# ---------------------------------------------------------------------------

REGEX_EMAIL = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
REGEX_TELEFONE = re.compile(
    r"(?:\+?55\s*)?(?:\(?\d{2}\)?[\s.-]?)?(?:9\s?)?\d{4}[\s.-]?\d{4}"
)
REGEX_LINKEDIN = re.compile(r"(?:linkedin\.com/in/[a-zA-Z0-9\-_/]+)", re.IGNORECASE)

CIDADES_COMUNS = [
    "São Paulo", "Rio de Janeiro", "Belo Horizonte", "Curitiba", "Porto Alegre",
    "Salvador", "Fortaleza", "Recife", "Brasília", "Campinas", "Florianópolis",
    "Goiânia", "Manaus", "Belém", "Vitória", "Natal", "João Pessoa", "Maceió",
    "Santos", "Sorocaba", "Ribeirão Preto", "Osasco", "Guarulhos", "Uberlândia",
    "Contagem", "Joinville", "Londrina", "Niterói", "São José dos Campos",
]

NIVEIS_FORMACAO = {
    "ensino medio": "Ensino Médio",
    "ensino fundamental": "Ensino Fundamental",
    "tecnologo": "Tecnólogo",
    "tecnico": "Técnico",
    "graduacao": "Graduação",
    "bacharelado": "Graduação",
    "licenciatura": "Graduação",
    "mba": "MBA",
    "pos graduacao": "Pós-graduação",
    "pos-graduacao": "Pós-graduação",
    "especializacao": "Pós-graduação",
    "mestrado": "Mestrado",
    "doutorado": "Doutorado",
    "phd": "Doutorado",
}

AREAS_FORMACAO = [
    "administracao", "economia", "direito", "psicologia", "recursos humanos",
    "engenharia", "contabilidade", "ciencias contabeis", "marketing",
    "sistemas de informacao", "ciencia da computacao", "publicidade",
    "pedagogia", "logistica", "gestao comercial", "gestao financeira",
    "relacoes internacionais", "comunicacao social", "medicina", "enfermagem",
]

IDIOMAS_CONHECIDOS = {
    "portugues": "Português",
    "ingles": "Inglês",
    "espanhol": "Espanhol",
    "frances": "Francês",
    "italiano": "Italiano",
    "alemao": "Alemão",
}

NIVEIS_IDIOMA = {
    "basico": "Básico",
    "intermediario": "Intermediário",
    "avancado": "Avançado",
    "fluente": "Fluente",
    "nativo": "Nativo",
}

# Títulos de seção comuns que NUNCA devem ser confundidos com o nome do candidato.
ROTULOS_SECAO_IGNORAR = [
    "objetivo", "dados pessoais", "informacoes pessoais", "perfil profissional",
    "perfil", "resumo profissional", "resumo", "sobre mim", "apresentacao",
    "curriculum vitae", "curriculo", "contato", "formacao academica", "formacao",
    "experiencia profissional", "experiencia", "idiomas", "competencias",
    "habilidades", "cursos", "certificacoes", "referencias", "objetivo profissional",
    "qualificacoes", "identificacao",
]

# CORREÇÃO: campos de dados pessoais que nunca são o nome do candidato.
PREFIXOS_DADOS_PESSOAIS = [
    "estado civil", "nacionalidade", "data de nascimento",
    "nascimento", "cpf", "rg", "sexo", "genero",
]


def _parece_dado_pessoal(linha: str) -> bool:
    linha_norm = normalizar_texto(linha)
    return any(linha_norm.startswith(p) for p in PREFIXOS_DADOS_PESSOAIS)


def _parece_rotulo_de_secao(linha: str) -> bool:
    linha_norm = normalizar_texto(linha).rstrip(":").strip()
    for rotulo in ROTULOS_SECAO_IGNORAR:
        if linha_norm == rotulo or linha_norm.startswith(rotulo + ":") or linha_norm.startswith(rotulo + " "):
            return True
    return False


def extrair_nome(texto: str) -> str:
    linhas = [l.strip() for l in texto.splitlines() if l.strip()]
    candidatos_validos = []

    for linha in linhas[:8]:
        if REGEX_EMAIL.search(linha):
            continue
        if any(char.isdigit() for char in linha):
            continue
        if _parece_rotulo_de_secao(linha):
            continue
        if _parece_dado_pessoal(linha):
            continue
        palavras = linha.split()
        if 1 < len(palavras) <= 5 and all(p[0].isupper() for p in palavras if p.isalpha()):
            candidatos_validos.append(linha)

    if candidatos_validos:
        return candidatos_validos[0].title()

    # CORREÇÃO: fallback também filtra dados pessoais (corrige "Estado Civil: Solteira" virando nome)
    for linha in linhas:
        if not _parece_rotulo_de_secao(linha) and not _parece_dado_pessoal(linha):
            return linha.title()

    return "Não identificado"


def extrair_email(texto: str):
    m = REGEX_EMAIL.search(texto)
    return m.group(0) if m else None


def extrair_telefone(texto: str):
    m = REGEX_TELEFONE.search(texto)
    return m.group(0).strip() if m else None


def extrair_linkedin(texto: str):
    m = REGEX_LINKEDIN.search(texto)
    return "https://www." + m.group(0) if m and not m.group(0).startswith("http") else (m.group(0) if m else None)


def extrair_cidade(texto: str):
    texto_norm = normalizar_texto(texto)
    for cidade in CIDADES_COMUNS:
        if normalizar_texto(cidade) in texto_norm:
            return cidade

    # CORREÇÃO: fallback para padrão "Cidade - UF" ou "Cidade/UF"
    # cobre cidades fora da lista e formatos comuns em currículos brasileiros
    match = re.search(
        r"([A-ZÁÉÍÓÚÂÊÎÔÛÃÕÇ][a-záéíóúâêîôûãõç]+"
        r"(?:\s[A-ZÁÉÍÓÚÂÊÎÔÛÃÕÇ][a-záéíóúâêîôûãõç]+)*)"
        r"\s*[-/]\s*([A-Z]{2})\b",
        texto,
    )
    if match:
        return f"{match.group(1)} - {match.group(2)}"

    return None


def extrair_formacao(texto: str):
    texto_norm = normalizar_texto(texto)
    nivel_encontrado = None
    for chave, nivel in NIVEIS_FORMACAO.items():
        if chave in texto_norm:
            nivel_encontrado = nivel
            break

    area_encontrada = None
    for area in AREAS_FORMACAO:
        if area in texto_norm:
            area_encontrada = area.title()
            break

    return nivel_encontrado, area_encontrada


def extrair_idiomas(texto: str):
    texto_norm = normalizar_texto(texto)
    idiomas_encontrados = {}
    janela_maxima = 25

    for chave_idioma, nome_idioma in IDIOMAS_CONHECIDOS.items():
        if chave_idioma not in texto_norm:
            continue

        idx = texto_norm.find(chave_idioma)
        trecho = texto_norm[idx + len(chave_idioma): idx + len(chave_idioma) + janela_maxima]

        melhor_nivel, melhor_posicao = None, None
        for chave_nivel, nome_nivel in NIVEIS_IDIOMA.items():
            posicao = trecho.find(chave_nivel)
            if posicao != -1 and (melhor_posicao is None or posicao < melhor_posicao):
                melhor_posicao = posicao
                melhor_nivel = nome_nivel

        idiomas_encontrados[nome_idioma] = melhor_nivel or "Não especificado"
    return idiomas_encontrados


def extrair_competencias(texto: str, palavras_chave: list):
    encontradas = []
    evidencias = {}
    for palavra in palavras_chave:
        achou, evidencia = encontrar_ocorrencias(texto, palavra)
        if achou:
            encontradas.append(palavra)
            evidencias[palavra] = evidencia
    return encontradas, evidencias


PADRAO_INICIO_EXPERIENCIA = re.compile(
    r"(experi[êe]ncias?\s+profission(al|ais)|hist[óo]rico\s+profissional|"
    r"atua[çc][ãa]o\s+profissional|vida\s+profissional|experi[êe]ncias?\s+de\s+trabalho)",
    re.IGNORECASE
)

PADRAO_FIM_EXPERIENCIA = re.compile(
    r"(forma[çc][ãa]o(\s+acad[êe]mica)?|educa[çc][ãa]o|idiomas|cursos(\s+e\s+certifica[çc][õo]es)?|"
    r"certifica[çc][õo]es|habilidades|compet[êe]ncias|informa[çc][õo]es\s+adicionais|"
    r"refer[êe]ncias|entidades\s+estudantis|atividades\s+(extracurriculares|complementares)|"
    r"experi[êe]ncias?\s+extracurricular(es)?|experi[êe]ncias?\s+acad[êe]mica(s)?|"
    r"voluntariado|projetos\s+(sociais|acad[êe]micos))",
    re.IGNORECASE
)


def isolar_secao_experiencia(texto: str) -> str:
    match_inicio = PADRAO_INICIO_EXPERIENCIA.search(texto)
    if not match_inicio:
        return texto

    inicio_secao = match_inicio.end()
    match_fim = PADRAO_FIM_EXPERIENCIA.search(texto, pos=inicio_secao)
    fim_secao = match_fim.start() if match_fim else len(texto)

    trecho = texto[inicio_secao:fim_secao].strip()
    return trecho if trecho else texto


def calcular_experiencia_total(texto: str):
    secao_experiencia = isolar_secao_experiencia(texto)
    periodos = extrair_periodos(secao_experiencia)
    total_meses = calcular_meses_experiencia(periodos)
    return total_meses, formatar_experiencia(total_meses)


def processar_curriculo(nome_arquivo: str, arquivo_bytes: bytes, palavras_chave: list = None) -> dict:
    if palavras_chave is None:
        palavras_chave = listar_todas_palavras_chave_sugeridas()

    texto = extrair_texto(nome_arquivo, arquivo_bytes)

    if not texto or len(texto.strip()) < 20:
        raise ValueError(
            f"Não foi possível extrair texto de '{nome_arquivo}'. "
            "O arquivo pode ser uma imagem digitalizada sem OCR."
        )

    nivel_formacao, area_formacao = extrair_formacao(texto)
    idiomas = extrair_idiomas(texto)
    competencias, evidencias = extrair_competencias(texto, palavras_chave)
    total_meses, experiencia_formatada = calcular_experiencia_total(texto)

    dados = {
        "arquivo": nome_arquivo,
        "texto_bruto": texto,
        "nome": extrair_nome(texto),
        "email": extrair_email(texto),
        "telefone": extrair_telefone(texto),
        "linkedin": extrair_linkedin(texto),
        "cidade": extrair_cidade(texto),
        "formacao_nivel": nivel_formacao,
        "formacao_area": area_formacao,
        "idiomas": idiomas,
        "competencias_encontradas": competencias,
        "evidencias_competencias": evidencias,
        "experiencia_meses": total_meses,
        "experiencia_formatada": experiencia_formatada,
    }
    return dados
