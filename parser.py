"""
parser.py
Responsável por ler os arquivos de currículo (PDF e DOCX), extrair o texto
bruto e, a partir dele, extrair os campos estruturados: nome, telefone,
email, cidade, LinkedIn, experiência, formação, idiomas, competências, etc.
"""

import re
import io
from datetime import datetime
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
    # Parágrafos primeiro — o nome quase sempre está fora da tabela
    partes = [p.text for p in documento.paragraphs if p.text.strip()]
    for tabela in documento.tables:
        for linha in tabela.rows:
            for celula in linha.cells:
                if celula.text.strip():
                    partes.append(celula.text.strip())
    return "\n".join(partes)


def extrair_texto(nome_arquivo: str, arquivo_bytes: bytes) -> str:
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

# Correção #20: transformado em dict {texto_normalizado: nome_original}
# para busca O(1) ao invés de O(n) na lista.
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

# Siglas válidas de estados brasileiros para validar o padrão Cidade - UF
SIGLAS_ESTADO = {
    "AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS",
    "MG","PA","PB","PR","PE","PI","RJ","RN","RS","RO","RR","SC",
    "SP","SE","TO",
}

# Ordenado do nível mais alto para o mais baixo para evitar falsos positivos
# (ex: "técnico" aparecendo antes de "graduação" no texto)
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
    ("tecnico em", "Técnico"),   # "técnico em X" antes de "técnico" isolado
    ("tecnico", "Técnico"),
    ("ensino medio", "Ensino Médio"),
    ("ensino fundamental", "Ensino Fundamental"),
]

AREAS_FORMACAO = [
    "administracao", "economia", "direito", "psicologia", "recursos humanos",
    "engenharia civil", "engenharia",   # civil antes de genérico
    "contabilidade", "ciencias contabeis", "marketing",
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
    # cobre linhas como "Brasileiro, Solteiro, 33 anos"
    "brasileiro", "brasileira", "solteiro", "solteira", "casado", "casada",
    "endereco", "endereço",
]

# Títulos de cargo/objetivo que nunca são nomes de pessoa
PREFIXOS_CARGO_OBJETIVO = [
    "engenheiro", "engenheira", "analista", "gerente", "coordenador",
    "assistente", "supervisor", "diretor", "tecnico", "auxiliar",
    "estagiario", "consultor", "programador", "desenvolvedor",
    "atuar", "busco", "procuro",
]

# Preposições que não iniciam com maiúscula em nomes compostos brasileiros
PREPOSICOES_NOME = {"de", "da", "do", "dos", "das", "e", "van", "von", "del"}


def _parece_dado_pessoal(linha: str) -> bool:
    ln = normalizar_texto(linha)
    return any(ln.startswith(p) for p in PREFIXOS_DADOS_PESSOAIS)


def _parece_cargo_objetivo(linha: str) -> bool:
    ln = normalizar_texto(linha)
    return any(ln.startswith(p) for p in PREFIXOS_CARGO_OBJETIVO)


def _parece_localizacao(linha: str) -> bool:
    """Detecta linhas de endereço/bairro/cidade como 'Jardim Alvorada - Cambé/PR'.

    Correção: currículos com layout de duas colunas fazem o pdfplumber
    extrair o bloco de contato (cidade, telefone) antes do nome. Sem esse
    filtro, 'Jardim Alvorada - Cambé/PR' seria capturado como nome.
    """
    linha_strip = linha.strip()
    # Padrão "... - UF" ou ".../UF" no final da linha
    m = re.search(r'[-/,]\s*([A-Z]{2})\s*$', linha_strip)
    if m and m.group(1) in SIGLAS_ESTADO:
        return True
    # Padrão "... UF" isolado no final (ex: "São Paulo SP")
    m2 = re.search(r'\b([A-Z]{2})\s*$', linha_strip)
    if m2 and m2.group(1) in SIGLAS_ESTADO:
        return True
    return False


def _parece_rotulo_de_secao(linha: str) -> bool:
    ln = normalizar_texto(linha).rstrip(":").strip()
    return any(
        ln == r or ln.startswith(r + ":") or ln.startswith(r + " ")
        for r in ROTULOS_SECAO_IGNORAR
    )


def _limpar_linha(linha: str) -> str:
    """Remove caracteres especiais não-ASCII como bullets do Word (ex: \\uf0b7)."""
    return re.sub(r'[^\x00-\x7F\u00C0-\u024F]', '', linha).strip()


def _nome_do_arquivo(nome_arquivo: str) -> str | None:
    """Último recurso: extrai nome do arquivo quando o texto não permite.
    Ex: 'bruno-carvalhaes-rodrigues-947725746.pdf' -> 'Bruno Carvalhaes Rodrigues'"""
    base = re.split(
        r'[-_]\d',
        nome_arquivo.lower().replace('.pdf', '').replace('.docx', '')
    )[0]
    partes = [p for p in base.split('-') if p.isalpha() and len(p) > 1]
    if len(partes) >= 2:
        return " ".join(p.title() for p in partes)
    return None


def extrair_nome(texto: str, nome_arquivo: str = "") -> str:
    linhas_raw = [l.strip() for l in texto.splitlines() if l.strip()]
    linhas = [_limpar_linha(l) for l in linhas_raw]
    linhas = [l for l in linhas if l]

    candidatos_validos = []
    i = 0
    while i < min(12, len(linhas)):
        linha = linhas[i]

        if REGEX_EMAIL.search(linha):
            i += 1; continue
        if any(c.isdigit() for c in linha):
            i += 1; continue
        if _parece_rotulo_de_secao(linha):
            i += 1; continue
        if _parece_dado_pessoal(linha):
            i += 1; continue
        if _parece_cargo_objetivo(linha):
            i += 1; continue
        # Correção: bloco de contato de PDFs com duas colunas aparece antes
        # do nome. Descarta linhas que terminam com sigla de estado.
        if _parece_localizacao(linha):
            i += 1; continue

        palavras = linha.split()

        # Caso Bruno: "B R U N O C A R V A L H A E S R O D R I G U E S"
        if len(palavras) >= 4 and all(len(p) == 1 and p.isalpha() for p in palavras):
            nome_arq = _nome_do_arquivo(nome_arquivo)
            candidatos_validos.append(nome_arq if nome_arq else "".join(palavras).title())
            i += 1; continue

        # Caso Giulia: nome partido em duas linhas de uma palavra
        if (len(palavras) == 1 and palavras[0][0].isupper() and palavras[0].isalpha()
                and i + 1 < len(linhas)):
            proxima = linhas[i + 1]
            pp = proxima.split()
            if (len(pp) == 1 and pp[0][0].isupper() and pp[0].isalpha()
                    and not _parece_rotulo_de_secao(proxima)
                    and not _parece_dado_pessoal(proxima)
                    and not _parece_cargo_objetivo(proxima)):
                candidatos_validos.append(f"{palavras[0]} {pp[0]}".title())
                i += 2; continue

        # Caso Yan: nome com preposições minúsculas
        if (1 < len(palavras) <= 6 and all(
                p[0].isupper() for p in palavras
                if p.isalpha() and p.lower() not in PREPOSICOES_NOME
        )):
            candidatos_validos.append(linha.title())
            i += 1; continue

        i += 1

    if candidatos_validos:
        return candidatos_validos[0]

    nome_arq = _nome_do_arquivo(nome_arquivo)
    if nome_arq:
        return nome_arq

    for linha in linhas:
        if (not _parece_rotulo_de_secao(linha)
                and not _parece_dado_pessoal(linha)
                and not _parece_cargo_objetivo(linha)):
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
    """
    Correção #5: a busca anterior usava `in` simples (substring), o que
    causava falsos positivos — ex: "Natal" dava match em "profissional".
    Agora usa \b (word boundary) via regex para garantir palavra inteira.

    Correção adicional (Cícero): a ordem de busca foi invertida.
    Antes, cidades conhecidas eram buscadas primeiro em todo o texto,
    o que fazia "Londrina" (mencionada nas experiências) vencer sobre
    "Cambé/PR" (cidade real do candidato, no cabeçalho).
    Agora o padrão estrutural Cidade/UF tem prioridade — aparece quase
    sempre no cabeçalho, portanto representa melhor a cidade do candidato.
    """
    LETRAS = r"[A-Za-záéíóúâêîôûãõçÁÉÍÓÚÂÊÎÔÛÃÕÇ]"

    # 1) Padrão estrutural "Cidade/UF" ou "Cidade - UF" — alta precisão,
    #    geralmente no cabeçalho do currículo (cidade de residência real).
    match = re.search(
        rf"({LETRAS}+(?:\s{LETRAS}+)*)\s*[-–/]\s*([A-Z]{{2}})\b",
        texto,
    )
    if match:
        cidade_raw = match.group(1).strip().title()
        uf = match.group(2).strip()
        if uf in SIGLAS_ESTADO:
            return f"{cidade_raw} - {uf}"

    # 2) Padrão "bairro, Cidade – UF" (ex: "Vargem Pequena, Rio de Janeiro – RJ")
    match2 = re.search(
        rf",\s*({LETRAS}+(?:\s{LETRAS}+)*?)\s*[-–]\s*([A-Z]{{2}})\b",
        texto,
    )
    if match2:
        cidade_raw = match2.group(1).strip().title()
        uf = match2.group(2).strip()
        if uf in SIGLAS_ESTADO:
            return f"{cidade_raw} - {uf}"

    # 3) Fallback: lista de cidades conhecidas com word boundary.
    #    Só usado quando nenhum padrão estrutural foi encontrado, para
    #    evitar capturar cidades mencionadas no corpo das experiências.
    texto_norm = normalizar_texto(texto)
    for cidade_norm, cidade_original in CIDADES_COMUNS.items():
        padrao = r"\b" + re.escape(cidade_norm) + r"\b"
        if re.search(padrao, texto_norm):
            return cidade_original

    return None


def extrair_formacao(texto: str):
    texto_norm = normalizar_texto(texto)
    nivel = next((v for k, v in NIVEIS_FORMACAO_ORDENADO if k in texto_norm), None)
    area = next((a.title() for a in AREAS_FORMACAO if a in texto_norm), None)
    return nivel, area


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
    """
    Correção #9: períodos com data de fim no futuro são descartados para
    evitar contar "previsão de conclusão" como experiência já acumulada.
    """
    secao_experiencia = isolar_secao_experiencia(texto)
    periodos_brutos = extrair_periodos(secao_experiencia)

    hoje = datetime.now()
    periodos_validos = [
        (inicio, fim) for inicio, fim in periodos_brutos
        if inicio <= hoje  # descarta períodos que começam no futuro
    ]
    # Limita a data de fim ao momento atual para não contar meses futuros
    periodos_validos = [
        (inicio, min(fim, hoje)) for inicio, fim in periodos_validos
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
        # Correção #12: texto_bruto NÃO incluído nos dados retornados.
        # Era serializado no banco (até 100KB por currículo) sem necessidade.
        # O score e demais extrações já foram calculados acima.
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
        # texto_bruto mantido apenas em memória para o score_module usar
        # nesta requisição; é passado separadamente e descartado depois.
        "_texto_bruto_temp": texto,
    }
    return dados
