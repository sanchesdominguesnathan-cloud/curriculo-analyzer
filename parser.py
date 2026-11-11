"""
parser.py
Responsável por ler os arquivos de currículo (PDF e DOCX), extrair o texto
bruto e, a partir dele, extrair os campos estruturados: nome, telefone,
email, cidade, LinkedIn, experiência, formação, idiomas, competências, etc.
"""

import re
import io
import unicodedata
from datetime import datetime
import pdfplumber
import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from utils import (
    normalizar_texto,
    extrair_periodos,
    calcular_meses_experiencia,
    formatar_experiencia,
    SIGLAS_ESTADO,
)
from keywords import (
    encontrar_ocorrencias,
    listar_todas_palavras_chave_sugeridas,
    BASE_SINONIMOS,
)

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


def _linhas_da_tabela(tabela) -> list:
    """Uma linha de texto por parágrafo de cada célula. Células mescladas
    aparecem várias vezes em `row.cells` (uma por coluna coberta); aqui cada
    célula é lida uma única vez, para não duplicar o texto."""
    linhas = []
    for linha in tabela.rows:
        vistas = set()
        for celula in linha.cells:
            if id(celula._tc) in vistas:
                continue
            vistas.add(id(celula._tc))
            for paragrafo in celula.paragraphs:
                if paragrafo.text.strip():
                    linhas.append(paragrafo.text.strip())
    return linhas


def extrair_texto_docx(arquivo_bytes: bytes) -> str:
    """Lê parágrafos e tabelas NA ORDEM em que aparecem no documento.

    Correção: antes todos os parágrafos vinham primeiro e as tabelas só no
    fim. Em modelos de currículo que colocam o nome/contato dentro de uma
    tabela, o nome ia parar depois de todo o resto do texto e não era achado
    entre as primeiras linhas."""
    documento = docx.Document(io.BytesIO(arquivo_bytes))
    partes = []
    for filho in documento.element.body.iterchildren():
        tag = filho.tag.rsplit("}", 1)[-1]
        if tag == "p":
            texto = Paragraph(filho, documento).text
            if texto.strip():
                partes.append(texto)
        elif tag == "tbl":
            partes.extend(_linhas_da_tabela(Table(filho, documento)))
    return "\n".join(partes)


def extrair_texto(nome_arquivo: str, arquivo_bytes: bytes) -> str:
    nome_lower = nome_arquivo.lower()
    if not (nome_lower.endswith(".pdf") or nome_lower.endswith(".docx")):
        raise ValueError(f"Formato de arquivo não suportado: {nome_arquivo}")
    try:
        if nome_lower.endswith(".pdf"):
            return extrair_texto_pdf(arquivo_bytes)
        return extrair_texto_docx(arquivo_bytes)
    except ValueError:
        raise
    except Exception as e:
        # Arquivo corrompido/protegido: PdfminerException, BadZipFile,
        # PackageNotFoundError etc. viram um ValueError com mensagem clara.
        raise ValueError(
            f"Não foi possível ler '{nome_arquivo}' (arquivo corrompido, protegido por senha "
            f"ou em formato inválido)."
        ) from e


# ---------------------------------------------------------------------------
# Extração de campos individuais via regex
# ---------------------------------------------------------------------------

REGEX_EMAIL = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
# Correção: sem as travas de dígito antes/depois, o padrão casava pedaços de
# números maiores (CPF, CEP, anos colados) — ver extrair_telefone.
REGEX_TELEFONE = re.compile(
    r"(?<![\d/])(?:\+?55[\s.-]*)?(?:\(?\d{2}\)?[\s.-]?)?(?:9[\s.-]?)?\d{4}[\s.-]?\d{4}(?![\d/])"
)
REGEX_LINKEDIN = re.compile(r"(?:linkedin\.com/in/[a-zA-Z0-9\-_/]+)", re.IGNORECASE)

_CIDADES_COMUNS_LISTA = [
    "São Paulo", "Rio de Janeiro", "Belo Horizonte", "Curitiba", "Porto Alegre",
    "Salvador", "Fortaleza", "Recife", "Brasília", "Campinas", "Florianópolis",
    "Goiânia", "Manaus", "Belém", "Vitória", "Natal", "João Pessoa", "Maceió",
    "Santos", "Sorocaba", "Ribeirão Preto", "Osasco", "Guarulhos", "Uberlândia",
    "Contagem", "Joinville", "Londrina", "Niterói", "São José dos Campos",
    "Itaboraí", "Maricá", "Duque de Caxias", "Nova Iguaçu", "São Gonçalo",
    "Taquara", "Vargem Pequena",
]
CIDADES_COMUNS = {normalizar_texto(c): c for c in _CIDADES_COMUNS_LISTA}

# Nível de formação -> rótulo. A ordem importa: vence o primeiro que aparecer.
NIVEIS_FORMACAO_ORDENADO = [
    ("doutorado", "Doutorado"),
    ("phd", "Doutorado"),
    ("mestrado", "Mestrado"),
    ("mba", "MBA"),
    ("pos graduacao", "Pós-graduação"),
    ("pos-graduacao", "Pós-graduação"),
    ("especializacao", "Pós-graduação"),
    ("bacharelado", "Graduação"),
    ("licenciatura", "Graduação"),
    ("graduacao", "Graduação"),
    ("tecnologo", "Tecnólogo"),
    ("tecnico em", "Técnico"),
    ("tecnico", "Técnico"),
    ("ensino medio", "Ensino Médio"),
    ("ensino fundamental", "Ensino Fundamental"),
]

# chave normalizada -> nome para exibição (antes saía "Administracao",
# "Gestao Comercial" etc. sem acento, direto no resumo do candidato).
# A ordem importa: "engenharia civil" precisa vir antes de "engenharia".
AREAS_FORMACAO = {
    "administracao": "Administração",
    "economia": "Economia",
    "direito": "Direito",
    "psicologia": "Psicologia",
    "recursos humanos": "Recursos Humanos",
    "engenharia civil": "Engenharia Civil",
    "engenharia": "Engenharia",
    "contabilidade": "Contabilidade",
    "ciencias contabeis": "Ciências Contábeis",
    "marketing": "Marketing",
    "sistemas de informacao": "Sistemas de Informação",
    "ciencia da computacao": "Ciência da Computação",
    "publicidade": "Publicidade",
    "pedagogia": "Pedagogia",
    "logistica": "Logística",
    "gestao comercial": "Gestão Comercial",
    "gestao financeira": "Gestão Financeira",
    "relacoes internacionais": "Relações Internacionais",
    "comunicacao social": "Comunicação Social",
    "medicina": "Medicina",
    "enfermagem": "Enfermagem",
}


def _regex_palavra_inteira(chave: str):
    return re.compile(r"(?<![a-z0-9])" + re.escape(chave) + r"(?![a-z0-9])")


# Correção: a busca era por substring pura. "mba" casava dentro de
# "embalagem", "embarque", "combate"..., e currículos de logística/segurança
# ganhavam "MBA" (90 pontos de formação).
_NIVEIS_FORMACAO_REGEX = [(_regex_palavra_inteira(k), v) for k, v in NIVEIS_FORMACAO_ORDENADO]
_AREAS_FORMACAO_REGEX = [(_regex_palavra_inteira(k), v) for k, v in AREAS_FORMACAO.items()]

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

# ---------------------------------------------------------------------------
# Extração do NOME
# ---------------------------------------------------------------------------

ROTULOS_SECAO_IGNORAR = [
    "objetivo", "dados pessoais", "informacoes pessoais", "perfil profissional",
    "perfil", "resumo profissional", "resumo", "sobre mim", "apresentacao",
    "curriculum vitae", "curriculo", "contato", "formacao academica", "formacao",
    "experiencia profissional", "experiencia", "idiomas", "competencias",
    "habilidades", "cursos", "certificacoes", "referencias", "objetivo profissional",
    "qualificacoes", "identificacao",
]

PREFIXOS_DADOS_PESSOAIS = [
    "estado civil", "nacionalidade", "data de nascimento", "nascimento",
    "cpf", "rg", "sexo", "genero",
    "brasileiro", "brasileira", "solteiro", "solteira", "casado", "casada",
    "endereco",
]

PREFIXOS_CARGO_OBJETIVO = [
    "engenheiro", "engenheira", "analista", "gerente", "coordenador",
    "assistente", "supervisor", "diretor", "tecnico", "auxiliar",
    "estagiario", "consultor", "programador", "desenvolvedor",
    "atuar", "busco", "procuro",
]

PREPOSICOES_NOME = {
    "de", "da", "do", "dos", "das", "e", "di", "du", "del", "della",
    "van", "von", "der", "den", "la", "le", "y",
}

# Palavras que NUNCA fazem parte de um nome de pessoa: títulos de seção,
# rótulos de contato e termos de currículo. Correção: o "caso geral" aceitava
# QUALQUER linha curta com iniciais maiúsculas, então "Informações de
# Contato", "Dados Profissionais", "Carteira de Habilitação" ou "Redes
# Sociais" viravam o nome do candidato.
PALAVRAS_NAO_NOME = {
    "curriculo", "curriculum", "vitae", "cv", "resume", "resumo", "perfil",
    "profile", "objetivo", "objetivos", "objective", "dados", "pessoais",
    "pessoal", "profissional", "profissionais", "informacoes", "informacao",
    "contato", "contatos", "contact", "formacao", "educacao", "education",
    "escolaridade", "academica", "academico", "academicos", "experiencia",
    "experiencias", "experience", "idiomas", "idioma", "languages", "language",
    "cursos", "curso", "certificacoes", "certificacao", "certificados",
    "habilidades", "skills", "competencias", "qualificacoes", "referencias",
    "projetos", "atividades", "complementares", "extracurriculares",
    "voluntariado", "carteira", "habilitacao", "redes", "sociais",
    "disponibilidade", "pretensao", "salarial", "endereco", "telefone",
    "celular", "fone", "tel", "cel", "email", "mail", "whatsapp", "linkedin",
    "github", "portfolio", "sobre", "mim", "apresentacao", "identificacao",
    "tecnologias", "ferramentas", "conquistas", "publicacoes", "premios",
    "adicionais", "adicional", "summary", "resumo", "pagina", "page",
}


def _tokens_competencia() -> set:
    """Palavras das áreas/competências conhecidas (ex.: "Gestão Financeira",
    "Recursos Humanos", "Marketing Digital") — também não são nomes."""
    tokens = set()
    for chave, variacoes in BASE_SINONIMOS.items():
        for texto in [chave, *variacoes]:
            for t in re.findall(r"[a-z]+", normalizar_texto(texto)):
                if len(t) >= 5:
                    tokens.add(t)
    for chave in AREAS_FORMACAO:
        for t in re.findall(r"[a-z]+", chave):
            if len(t) >= 5:
                tokens.add(t)
    return tokens


_TOKENS_NAO_NOME = PALAVRAS_NAO_NOME | _tokens_competencia()

_REGEX_PALAVRA_NOME = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’.\-]*$")

_ACENTOS_SOLTOS = {
    "´": "́", "`": "̀", "^": "̂", "ˆ": "̂",
    "¨": "̈", "~": "̃", "˜": "̃", "¸": "̧",
}
_REGEX_ACENTO_SOLTO = re.compile(r"([´`^ˆ¨~˜¸])([A-Za-z])")
# Símbolos, caracteres de controle/formatação e "private use" (bullets do
# Word como ) são descartados; letras, marcas e pontuação ficam.
_CATEGORIAS_REMOVIDAS = {"Cc", "Cf", "Co", "Cs", "Cn", "So", "Sk", "Sm", "Sc"}


def _match_inicio(padroes: list, linha: str) -> bool:
    ln = normalizar_texto(linha)
    return any(re.match(r"^" + re.escape(p) + r"\b", ln) for p in padroes)


def _parece_dado_pessoal(linha: str) -> bool:
    return _match_inicio(PREFIXOS_DADOS_PESSOAIS, linha)


def _parece_cargo_objetivo(linha: str) -> bool:
    ln = normalizar_texto(linha)
    primeira = ln.split()[0] if ln.split() else ""
    return any(primeira.startswith(p) for p in PREFIXOS_CARGO_OBJETIVO)


def _tem_palavra_nao_nome(linha: str) -> bool:
    return any(t in _TOKENS_NAO_NOME for t in re.findall(r"[a-z]+", normalizar_texto(linha)))


def _parece_localizacao(linha: str) -> bool:
    """Detecta linhas de endereço como 'Jardim Alvorada - Cambé/PR' ou
    'Niterói, RJ'.

    Correção: a versão anterior comparava com a linha normalizada em
    minúsculas e aceitava QUALQUER última palavra de 2 letras que coincidisse
    com uma sigla de estado — "Ana Paula Ma" (MA), "Luiz Pi" (PI) etc. eram
    descartados como endereço, e o nome verdadeiro se perdia. Agora a sigla
    precisa estar em MAIÚSCULAS no texto original e vir depois de uma cidade."""
    t = linha.strip()
    if not t:
        return False
    m = re.search(r"[^\s\-–/,]\s*[-–/,]\s*([A-Z]{2})\s*$", t)
    if m and m.group(1) in SIGLAS_ESTADO:
        return True
    m2 = re.search(r"[A-Za-zÀ-ÿ]\s+([A-Z]{2})\s*$", t)
    return bool(m2 and m2.group(1) in SIGLAS_ESTADO)


def _parece_rotulo_de_secao(linha: str) -> bool:
    ln = normalizar_texto(linha).rstrip(":").strip()
    return any(
        ln == r or ln.startswith(r + ":") or ln.startswith(r + " ")
        for r in ROTULOS_SECAO_IGNORAR
    )


def _limpar_linha(linha: str) -> str:
    """Normaliza a linha preservando as letras do nome.

    Correção: o filtro antigo apagava tudo fora de ASCII + Latin-1/Latin
    Estendido. Isso removia (a) ligaduras de PDF como 'ﬁ' e 'ﬂ' — "Filipe"
    virava "lipe", "Flávio" virava "ávio"; (b) acentos decompostos (e + ´),
    deixando "José" como "Jose"; e (c) acentos "soltos" que alguns PDFs
    emitem antes da letra. Agora: acentos soltos são recompostos, NFKC
    expande ligaduras e compõe acentos, e só símbolos/controles saem."""
    linha = _REGEX_ACENTO_SOLTO.sub(lambda m: m.group(2) + _ACENTOS_SOLTOS[m.group(1)], linha)
    linha = unicodedata.normalize("NFKC", linha)
    linha = "".join(ch for ch in linha if unicodedata.category(ch) not in _CATEGORIAS_REMOVIDAS)
    linha = re.sub(r"[•·●○▪■□◦➢➤►▶]", " ", linha)
    linha = re.sub(r"\s+", " ", linha)
    return linha.strip(" -–—|:;,")


def _capitalizar_trechos(token: str) -> str:
    """'d'ávila' -> "D'Ávila"; 'ana-maria' -> 'Ana-Maria'."""
    return re.sub(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", lambda m: m.group(0).capitalize(), token)


def _formatar_nome(nome: str) -> str:
    """Capitaliza o nome sem os defeitos do str.title(): mantém "da/de/do/
    dos/das/e" em minúsculas (str.title() gerava "Maria Da Silva"), preserva
    caixa mista já correta ("McDonald", "DeLuca") e trata apóstrofo/hífen."""
    saida = []
    for i, token in enumerate(nome.split()):
        base = token.lower().strip(".")
        if i > 0 and base in PREPOSICOES_NOME:
            saida.append(base)
        elif token.isupper() or token.islower():
            saida.append(_capitalizar_trechos(token))
        else:
            saida.append(token)
    return " ".join(saida)


_RUIDO_ARQUIVO = {
    "cv", "curriculo", "curriculum", "vitae", "resume", "atualizado",
    "atualizada", "final", "novo", "nova", "copia", "copy", "vaga", "pdf",
    "docx", "doc", "versao", "ver",
}


def _nome_do_arquivo(nome_arquivo: str):
    """Tenta montar o nome a partir do nome do arquivo ('bruno-carvalhaes-123.pdf',
    'CV_Maria_Silva.docx'). Aceita separadores '-', '_', espaço e ponto
    (antes só o '-' funcionava)."""
    if not nome_arquivo:
        return None
    base = re.sub(r"\.(pdf|docx)$", "", nome_arquivo.strip(), flags=re.IGNORECASE)
    base = re.split(r"[-_\s.]*\(?\d", base)[0]
    tokens = [t for t in re.split(r"[-_\s.]+", base) if t.isalpha() and len(t) > 1]
    tokens = [t for t in tokens if normalizar_texto(t) not in _RUIDO_ARQUIVO]
    if not 2 <= len(tokens) <= 6:
        return None
    candidato = " ".join(tokens)
    if _tem_palavra_nao_nome(candidato):
        return None
    return _formatar_nome(candidato)


def _remover_rotulo_nome(linha: str) -> str:
    return re.sub(r"^(?:nome(?:\s+completo)?|name)\s*[:\-–]\s*", "", linha, flags=re.IGNORECASE)


def _validar_nome(linha: str, estrito: bool):
    """Retorna o nome formatado se a linha parecer nome de pessoa, senão None.

    estrito: exige >= 2 palavras (fora preposições), todas com inicial
    maiúscula. Relaxado: aceita nome de uma palavra ou em minúsculas."""
    linha = _remover_rotulo_nome(linha)
    tokens = linha.split()
    if not tokens or len(tokens) > 6:
        return None
    if any(c.isdigit() for c in linha) or "@" in linha:
        return None
    if not all(_REGEX_PALAVRA_NOME.match(t) for t in tokens):
        return None
    if _tem_palavra_nao_nome(linha):
        return None
    if _parece_dado_pessoal(linha) or _parece_cargo_objetivo(linha) or _parece_rotulo_de_secao(linha):
        return None
    significativos = [t for t in tokens if t.lower().strip(".") not in PREPOSICOES_NOME]
    if estrito:
        if len(significativos) < 2 or not all(t[0].isupper() for t in significativos):
            return None
    elif not significativos:
        return None
    return _formatar_nome(linha)


def _validar_nome_na_linha(linha: str, estrito: bool):
    """Testa a linha inteira e, se ela juntar nome e cargo/cidade com ' - ',
    cada pedaço ('Maria Silva - Analista Financeiro')."""
    for parte in re.split(r"\s+[-–—]\s+", linha):
        nome = _validar_nome(parte.strip(), estrito)
        if nome:
            return nome
    return None


def _letras_espacadas(linha_bruta: str):
    """'B R U N O   C A R V A L H A E S' -> ['BRUNO', 'CARVALHAES'].
    Com espaço simples entre todas as letras não dá para saber onde uma
    palavra termina: devolve uma única palavra."""
    tokens = linha_bruta.split()
    if len(tokens) >= 4 and all(len(t) == 1 and t.isalpha() for t in tokens):
        palavras = [re.sub(r"\s+", "", p) for p in re.split(r"\s{2,}", linha_bruta.strip())]
        palavras = [p for p in palavras if p]
        if palavras and not _tem_palavra_nao_nome(" ".join(palavras)):
            return palavras
    return None


def _itens_do_cabecalho(texto: str, limite: int = 12) -> list:
    """Primeiras linhas úteis do currículo como lista de (tipo, valor).

    PDFs de duas colunas juntam, na mesma linha, texto das duas colunas
    separado por vários espaços; cada coluna vira um item. Linhas de
    localização são descartadas aqui (bloco de contato costuma vir antes do
    nome)."""
    itens = []
    for bruta in texto.splitlines():
        bruta = bruta.strip()
        if not bruta:
            continue
        espacadas = _letras_espacadas(bruta)
        if espacadas:
            itens.append(("espacado", espacadas))
            continue
        for coluna in re.split(r"\s{3,}|\t|\s*[|•·●]\s*", bruta):
            coluna = coluna.strip()
            if not coluna or _parece_localizacao(coluna):
                continue
            limpa = _limpar_linha(coluna)
            if limpa:
                itens.append(("texto", limpa))
    return itens[:limite]


def extrair_nome(texto: str, nome_arquivo: str = "") -> str:
    itens = _itens_do_cabecalho(texto)

    # 1) Passe estrito, na ordem em que as linhas aparecem
    for i, (tipo, valor) in enumerate(itens):
        if tipo == "espacado":
            if len(valor) == 1:
                return _nome_do_arquivo(nome_arquivo) or _formatar_nome(valor[0])
            return _formatar_nome(" ".join(valor))

        nome = _validar_nome_na_linha(valor, estrito=True)
        if nome:
            return nome

        # Nome partido em duas linhas: "Giulia" / "Santos"
        if (len(valor.split()) == 1 and i + 1 < len(itens) and itens[i + 1][0] == "texto"):
            proxima = itens[i + 1][1]
            if len(proxima.split()) == 1:
                partido = _validar_nome(f"{valor} {proxima}", estrito=True)
                if partido:
                    return partido

    # 2) Nome do arquivo
    nome_arq = _nome_do_arquivo(nome_arquivo)
    if nome_arq:
        return nome_arq

    # 3) Passe relaxado (nome em minúsculas ou de uma palavra só). Correção: o
    # fallback antigo devolvia "a primeira linha que sobrasse" — que podia ser
    # o e-mail ("Ana@X.Com"), o telefone ou um endereço. Agora a linha precisa
    # passar nas mesmas validações; sem candidato, devolve "Não identificado".
    for tipo, valor in itens:
        if tipo != "texto":
            continue
        nome = _validar_nome_na_linha(valor, estrito=False)
        if nome:
            return nome

    return "Não identificado"


# ---------------------------------------------------------------------------
# Demais campos
# ---------------------------------------------------------------------------

def extrair_email(texto: str):
    m = REGEX_EMAIL.search(texto)
    return m.group(0) if m else None


_ANO = r"(?:19|20)\d{2}"


def extrair_telefone(texto: str):
    """Correção: o regex devolvia o primeiro grupo de 8 dígitos do texto —
    '2018-2020' (período de um emprego) virava o telefone do candidato quando
    aparecia antes do telefone de verdade. Agora intervalos de anos são
    descartados e números com DDD têm preferência."""
    candidatos = []
    for m in REGEX_TELEFONE.finditer(texto):
        bruto = m.group(0).strip()
        digitos = re.sub(r"\D", "", bruto)
        if re.fullmatch(_ANO + r"\D*" + _ANO, bruto):
            continue
        if 8 <= len(digitos) <= 13:
            candidatos.append((bruto, digitos))
    for bruto, digitos in candidatos:
        if len(digitos) >= 10:
            return bruto
    return candidatos[0][0] if candidatos else None


def extrair_linkedin(texto: str):
    m = REGEX_LINKEDIN.search(texto)
    return "https://www." + m.group(0) if m and not m.group(0).startswith("http") else (m.group(0) if m else None)


def extrair_cidade(texto: str):
    LETRAS = r"[A-Za-záéíóúâêîôûãõçÁÉÍÓÚÂÊÎÔÛÃÕÇ]"

    # 1) Padrão estrutural Cidade/UF ou Cidade - UF (geralmente no cabeçalho)
    match = re.search(
        rf"({LETRAS}+(?:\s{LETRAS}+)*)\s*[-–/]\s*([A-Z]{{2}})\b",
        texto,
    )
    if match:
        cidade_raw = match.group(1).strip().title()
        uf = match.group(2).strip()
        if uf in SIGLAS_ESTADO:
            return f"{cidade_raw} - {uf}"

    # 2) Padrão "bairro, Cidade – UF"
    match2 = re.search(
        rf",\s*({LETRAS}+(?:\s{LETRAS}+)*?)\s*[-–]\s*([A-Z]{{2}})\b",
        texto,
    )
    if match2:
        cidade_raw = match2.group(1).strip().title()
        uf = match2.group(2).strip()
        if uf in SIGLAS_ESTADO:
            return f"{cidade_raw} - {uf}"

    # 3) Fallback: lista de cidades conhecidas com word boundary
    texto_norm = normalizar_texto(texto)
    for cidade_norm, cidade_original in CIDADES_COMUNS.items():
        padrao = r"\b" + re.escape(cidade_norm) + r"\b"
        if re.search(padrao, texto_norm):
            return cidade_original

    return None


def extrair_formacao(texto: str):
    texto_norm = normalizar_texto(texto)
    nivel = next((v for padrao, v in _NIVEIS_FORMACAO_REGEX if padrao.search(texto_norm)), None)
    area = next((v for padrao, v in _AREAS_FORMACAO_REGEX if padrao.search(texto_norm)), None)
    return nivel, area


def extrair_idiomas(texto: str):
    texto_norm = normalizar_texto(texto)
    idiomas_encontrados = {}
    janela_maxima = 25

    for chave_idioma, nome_idioma in IDIOMAS_CONHECIDOS.items():
        ocorrencias = list(_regex_palavra_inteira(chave_idioma).finditer(texto_norm))
        if not ocorrencias:
            continue
        # Usa a primeira ocorrência que venha acompanhada de um nível
        # (ex.: "Inglês" citado numa experiência e, mais abaixo, "Inglês:
        # avançado" na seção de idiomas).
        nivel_encontrado = None
        for ocorrencia in ocorrencias:
            trecho = texto_norm[ocorrencia.end(): ocorrencia.end() + janela_maxima]
            melhor_nivel, melhor_posicao = None, None
            for chave_nivel, nome_nivel in NIVEIS_IDIOMA.items():
                posicao = trecho.find(chave_nivel)
                if posicao != -1 and (melhor_posicao is None or posicao < melhor_posicao):
                    melhor_posicao = posicao
                    melhor_nivel = nome_nivel
            if melhor_nivel:
                nivel_encontrado = melhor_nivel
                break
        idiomas_encontrados[nome_idioma] = nivel_encontrado or "Não especificado"
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


# ---------------------------------------------------------------------------
# Experiência profissional
# ---------------------------------------------------------------------------

# Títulos de seção são reconhecidos só quando ocupam a LINHA INTEIRA. Antes,
# o fim da seção de experiência era a primeira ocorrência de "habilidades",
# "competências", "formação", "cursos"... em QUALQUER lugar do texto — mesmo
# no meio de uma frase ("desenvolvi habilidades de comunicação") — e os
# empregos seguintes eram descartados (um currículo de 7 anos saía com 1 ano
# e 11 meses).
_TITULO_EXPERIENCIA = re.compile(
    r"^(?:experiencias?(?:\s+(?:profissionais?|de\s+trabalho|anteriores?))?|"
    r"historico\s+profissional|atuacao\s+profissional|vida\s+profissional|"
    r"trajetoria\s+profissional|carreira(?:\s+profissional)?|"
    r"(?:work|professional)\s+experience|experience|employment\s+history)$"
)
_TITULO_FORMACAO = re.compile(
    r"^(?:formacao|educacao|escolaridade|dados\s+academicos|cursos?|idiomas?|"
    r"certificacoes?|certificados?|experiencias?\s+(?:extracurriculares?|academicas?)|"
    r"atividades\s+(?:extracurriculares|complementares)|entidades\s+estudantis|"
    r"voluntariado|projetos\s+(?:sociais|academicos)|education|courses|"
    r"certifications|languages)(?:\s+[a-z]+){0,3}$"
)
_TITULO_OUTRO = re.compile(
    r"^(?:objetivos?|resumo|perfil|sobre\s+mim|apresentacao|dados\s+pessoais|"
    r"informacoes\s+(?:pessoais|adicionais|complementares)|contato|habilidades|"
    r"competencias|qualificacoes|referencias|projetos|publicacoes|premios|"
    r"skills|summary|profile|references)(?:\s+[a-z]+){0,3}$"
)


def _tipo_titulo(linha: str):
    ln = normalizar_texto(re.sub(r"^[^\w]+", "", linha)).rstrip(" :.-").strip()
    if not ln or len(ln) > 60:
        return None
    if _TITULO_EXPERIENCIA.match(ln):
        return "exp"
    if _TITULO_FORMACAO.match(ln):
        return "formacao"
    if _TITULO_OUTRO.match(ln):
        return "outro"
    return None


def isolar_secao_experiencia(texto: str) -> str:
    """Devolve só o trecho de experiência profissional.

    - Com título de experiência: o conteúdo até o próximo título de seção.
    - Sem título de experiência: o texto inteiro MENOS as seções de formação/
      cursos/idiomas (antes os anos da faculdade eram somados como emprego).
    - Sem nenhum título reconhecido (ou trecho vazio): o texto inteiro."""
    linhas = texto.splitlines()
    tipos = [_tipo_titulo(ln) for ln in linhas]

    if "exp" in tipos:
        selecionadas, dentro = [], False
        for linha, tipo in zip(linhas, tipos):
            if tipo == "exp":
                dentro = True
            elif tipo:
                dentro = False
            elif dentro:
                selecionadas.append(linha)
        trecho = "\n".join(selecionadas).strip()
        return trecho or texto

    if "formacao" in tipos:
        selecionadas, ignorar = [], False
        for linha, tipo in zip(linhas, tipos):
            if tipo == "formacao":
                ignorar = True
            elif tipo:
                ignorar = False
            elif not ignorar:
                selecionadas.append(linha)
        trecho = "\n".join(selecionadas).strip()
        return trecho or texto

    return texto


def calcular_experiencia_total(texto: str):
    secao_experiencia = isolar_secao_experiencia(texto)
    periodos_brutos = extrair_periodos(secao_experiencia)

    hoje = datetime.now()
    periodos_validos = [
        (inicio, min(fim, hoje))
        for inicio, fim in periodos_brutos
        if inicio <= hoje
    ]

    total_meses = calcular_meses_experiencia(periodos_validos)
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
        "nome": extrair_nome(texto, nome_arquivo),
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
        "_texto_bruto_temp": texto,
    }
    return dados
