"""
utils.py
Funções utilitárias compartilhadas entre os módulos do sistema:
- Normalização de texto
- Cálculo de tempo entre datas (experiência)
- Geração de hash para evitar duplicidade de currículos
- Geração de resumo automático do candidato
"""

import re
import hashlib
import unicodedata
from datetime import datetime

SIGLAS_ESTADO = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
}


def normalizar_texto(texto: str) -> str:
    """Remove acentos, coloca em minúsculas e limpa espaços extras."""
    if not texto:
        return ""
    texto = texto.lower().strip()
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("utf-8")
    texto = re.sub(r"\s+", " ", texto)
    return texto


def gerar_hash_arquivo(conteudo_binario: bytes) -> str:
    """Gera um hash único (SHA-256) para o conteúdo do arquivo,
    usado para evitar analisar o mesmo currículo duas vezes."""
    return hashlib.sha256(conteudo_binario).hexdigest()


# ---------------------------------------------------------------------------
# Datas e períodos de experiência
# ---------------------------------------------------------------------------

MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
    # Inglês (currículos bilíngues: "Jan 2020 - Dec 2022")
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "feb": 2, "apr": 4, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "dec": 12,
}

# Correção: antes o "mês por extenso" aceitava QUALQUER palavra de 3 a 10
# letras antes do ano (ex.: "Itaú 2018", "SAP 2019"). Essas palavras não eram
# meses, caíam num parser genérico (dateutil, fuzzy=True) que preenchia o mês
# e o dia com os de HOJE — a experiência calculada mudava conforme o dia em
# que o currículo era analisado. Agora só nomes de mês reais são aceitos.
_MESES_ALT = "|".join(sorted((re.escape(m) for m in MESES_PT), key=len, reverse=True))
_MES_ANO = (
    r"(?<![a-zçãáéíóúâêôà])(?:" + _MESES_ALT + r")\.?\s*(?:de\s+)?[/\-]?\s*\d{4}"
)
_DATA = r"(?:\d{1,2}[/\-]\d{4}|" + _MES_ANO + r"|\d{4})"
_FIM_ATUAL = r"atualmente|atualidade|atual|presente|hoje|current|present|now|o\s+momento|andamento"

PADRAO_PERIODO = re.compile(
    r"(?P<inicio>" + _DATA + r")"
    r"\s*(?:a|até|ate|-|–|—|to)\s*"
    r"(?P<fim>" + _DATA + r"|" + _FIM_ATUAL + r")",
    re.IGNORECASE,
)

_ANO_MINIMO = 1950


def _ano_valido(ano: int) -> bool:
    return _ANO_MINIMO <= ano <= datetime.now().year + 1


def _parse_data_flexivel(texto_data: str, fim_periodo=False):
    """Converte uma string de data (formatos PT-BR/EN) em datetime.
    Retorna None se não reconhecer — nunca "chuta" usando a data de hoje."""
    t = re.sub(r"\s+", " ", texto_data.strip().lower())

    if re.fullmatch(_FIM_ATUAL, t):
        return datetime.now()

    # mês por extenso/abreviado + ano: "janeiro 2020", "jan/2020", "janeiro de 2020"
    m = re.match(r"(" + _MESES_ALT + r")\.?\s*(?:de\s+)?[/\-]?\s*(\d{4})$", t)
    if m:
        mes = MESES_PT.get(m.group(1))
        ano = int(m.group(2))
        if mes and _ano_valido(ano):
            return datetime(ano, mes, 1)
        return None

    # numérico mm/aaaa
    m = re.match(r"(\d{1,2})[/\-](\d{4})$", t)
    if m:
        mes, ano = int(m.group(1)), int(m.group(2))
        if 1 <= mes <= 12 and _ano_valido(ano):
            return datetime(ano, mes, 1)
        return None

    # apenas ano
    m = re.match(r"^(\d{4})$", t)
    if m:
        ano = int(m.group(1))
        if _ano_valido(ano):
            return datetime(ano, 12 if fim_periodo else 1, 1)
        return None

    return None


def extrair_periodos(texto: str):
    """Encontra todos os períodos (ex: 'jan/2020 a dez/2022' ou
    'janeiro de 2021 a dezembro de 2023') em um texto e retorna lista de
    tuplas (data_inicio, data_fim)."""
    periodos = []
    for match in PADRAO_PERIODO.finditer(texto):
        inicio = _parse_data_flexivel(match.group("inicio"))
        fim = _parse_data_flexivel(match.group("fim"), fim_periodo=True)
        if inicio and fim and fim >= inicio:
            periodos.append((inicio, fim))
    return periodos


def calcular_meses_experiencia(periodos):
    """Recebe lista de tuplas (inicio, fim) e retorna total de meses,
    mesclando períodos sobrepostos para não contar experiência em duplicidade.

    A contagem é inclusiva: 'jan/2018 a dez/2020' são 36 meses (antes dava 35,
    e 'jun a jun' dava 0). Assim 'jan a jun' + 'jul a dez' somam 12 meses."""
    if not periodos:
        return 0

    periodos_ordenados = sorted(periodos, key=lambda p: p[0])
    mesclados = [periodos_ordenados[0]]

    for inicio, fim in periodos_ordenados[1:]:
        ultimo_inicio, ultimo_fim = mesclados[-1]
        if inicio <= ultimo_fim:
            mesclados[-1] = (ultimo_inicio, max(ultimo_fim, fim))
        else:
            mesclados.append((inicio, fim))

    total_meses = 0
    for inicio, fim in mesclados:
        diff = (fim.year - inicio.year) * 12 + (fim.month - inicio.month) + 1
        total_meses += max(diff, 1)
    return total_meses


def formatar_experiencia(total_meses: int) -> str:
    """Converte meses totais em texto legível: 'X anos e Y meses'."""
    anos = total_meses // 12
    meses = total_meses % 12
    partes = []
    if anos:
        partes.append(f"{anos} ano{'s' if anos != 1 else ''}")
    if meses:
        partes.append(f"{meses} mes{'es' if meses != 1 else ''}")
    if not partes:
        return "Não identificado"
    return " e ".join(partes)


# ---------------------------------------------------------------------------
# Resumo automático
# ---------------------------------------------------------------------------

_NAO_INFORMADO = {"", "nao identificado", "nao informado", "nao especificado", "nao identificada"}


def _informado(valor) -> bool:
    """True se o valor tem conteúdo real (não vazio e não um 'Não identificado')."""
    return bool(valor) and normalizar_texto(str(valor)) not in _NAO_INFORMADO


def gerar_resumo_candidato(dados: dict) -> str:
    """Gera um resumo automático de poucas linhas sobre o candidato,
    com base nos dados extraídos do currículo (sem depender de API externa).

    Correções: (1) 'Não identificado' era tratado como experiência válida e
    gerava frases como 'possui aproximadamente Não identificado de
    experiência'; (2) o nome 'Não identificado' virava sujeito da frase;
    (3) idiomas sem nível saíam como 'Inglês (Não especificado)'."""
    nome = dados.get("nome")
    sujeito = nome if _informado(nome) else "Candidato(a)"

    exp_txt = dados.get("experiencia_formatada")
    competencias = dados.get("competencias_encontradas") or []
    formacao = dados.get("formacao_nivel") or ""
    area_formacao = dados.get("formacao_area") or ""
    idiomas = dados.get("idiomas") or {}

    frases = []

    if _informado(exp_txt):
        frases.append(f"{sujeito} possui aproximadamente {exp_txt} de experiência profissional.")
    else:
        frases.append(f"{sujeito}: experiência profissional não identificada no currículo.")

    if competencias:
        frases.append(f"Principais competências: {', '.join(competencias[:4])}.")
    else:
        frases.append("Principais competências: não identificadas.")

    if formacao and area_formacao:
        frases.append(f"Formação em {area_formacao} ({formacao}).")
    elif formacao:
        frases.append(f"Formação: {formacao}.")

    if idiomas:
        idiomas_txt = ", ".join(
            f"{idioma} ({nivel})" if _informado(nivel) else idioma
            for idioma, nivel in idiomas.items()
        )
        frases.append(f"Idiomas: {idiomas_txt}.")

    return re.sub(r"\s+", " ", " ".join(frases)).strip()
