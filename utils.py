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
from dateutil import parser as dateparser


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


MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}

# Classe de caracteres usada para nomes de mês por extenso. Inclui letras
# acentuadas (ex: "março") porque extrair_periodos roda sobre o texto
# ORIGINAL do currículo, ainda não normalizado (sem remover acentos).
_LETRAS_MES = r"a-zçãáéíóúâêôà"

PADRAO_PERIODO = re.compile(
    r"(?P<inicio>(\d{1,2}[/\-]\d{4})|(\d{4})|([" + _LETRAS_MES + r"]{3,10}\s*(?:de\s+)?[/\-]?\s*\d{4}))"
    r"\s*(?:a|até|-|–|—)\s*"
    r"(?P<fim>(\d{1,2}[/\-]\d{4})|(\d{4})|([" + _LETRAS_MES + r"]{3,10}\s*(?:de\s+)?[/\-]?\s*\d{4})|atual|presente|atualmente)",
    re.IGNORECASE
)


def _parse_data_flexivel(texto_data: str, fim_periodo=False):
    """Tenta converter uma string de data (varios formatos PT-BR) em objeto datetime."""
    texto_data = texto_data.strip().lower()
    if texto_data in ("atual", "presente", "atualmente", "hoje"):
        return datetime.now()

    # formato mes/ano por extenso: "janeiro 2020", "jan/2020" ou, escrito em
    # prosa, "janeiro de 2020" (com a preposição "de" entre o mês e o ano —
    # muito comum em currículos colados como texto corrido, ex: Google Forms).
    m = re.match(r"([" + _LETRAS_MES + r"]+)\s*(?:de\s+)?[/\-]?\s*(\d{4})", texto_data)
    if m:
        mes_txt, ano_txt = m.groups()
        mes_txt_norm = normalizar_texto(mes_txt)
        mes = MESES_PT.get(mes_txt_norm)
        if mes:
            try:
                return datetime(int(ano_txt), mes, 1)
            except ValueError:
                pass

    # formato numerico mm/aaaa
    m = re.match(r"(\d{1,2})[/\-](\d{4})", texto_data)
    if m:
        mes, ano = m.groups()
        try:
            return datetime(int(ano), int(mes), 1)
        except ValueError:
            pass

    # apenas ano
    m = re.match(r"^(\d{4})$", texto_data)
    if m:
        ano = int(m.group(1))
        mes = 12 if fim_periodo else 1
        return datetime(ano, mes, 1)

    # tenta parser genérico como último recurso
    try:
        return dateparser.parse(texto_data, fuzzy=True, dayfirst=True)
    except Exception:
        return None


def extrair_periodos(texto: str):
    """Encontra todos os períodos (ex: 'jan/2020 a dez/2022' ou
    'janeiro de 2021 a dezembro de 2023') em um texto e retorna lista de
    tuplas (data_inicio, data_fim)."""
    periodos = []
    for match in PADRAO_PERIODO.finditer(texto):
        inicio_txt = match.group("inicio")
        fim_txt = match.group("fim")
        inicio = _parse_data_flexivel(inicio_txt)
        fim = _parse_data_flexivel(fim_txt, fim_periodo=True)
        if inicio and fim and fim >= inicio:
            periodos.append((inicio, fim))
    return periodos


def calcular_meses_experiencia(periodos):
    """Recebe lista de tuplas (inicio, fim) e retorna total de meses,
    mesclando períodos sobrepostos para não contar experiência em duplicidade."""
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
        diff = (fim.year - inicio.year) * 12 + (fim.month - inicio.month)
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


def gerar_resumo_candidato(dados: dict) -> str:
    """Gera um resumo automático de poucas linhas sobre o candidato,
    com base nos dados extraídos do currículo (sem depender de API externa)."""
    nome = dados.get("nome") or "Profissional"
    exp_txt = dados.get("experiencia_formatada") or "experiência não identificada"
    competencias = dados.get("competencias_encontradas") or []
    formacao = dados.get("formacao_nivel") or ""
    area_formacao = dados.get("formacao_area") or ""
    idiomas = dados.get("idiomas") or {}

    top_competencias = ", ".join(competencias[:4]) if competencias else "não identificadas"

    frase_formacao = ""
    if formacao and area_formacao:
        frase_formacao = f"Formação em {area_formacao} ({formacao})."
    elif formacao:
        frase_formacao = f"Formação: {formacao}."

    frase_idiomas = ""
    if idiomas:
        idiomas_txt = ", ".join(
            f"{idioma} ({nivel})" if nivel else idioma
            for idioma, nivel in idiomas.items()
        )
        frase_idiomas = f"Idiomas: {idiomas_txt}."

    resumo = (
        f"{nome} possui aproximadamente {exp_txt} de experiência profissional. "
        f"Principais competências: {top_competencias}. "
        f"{frase_formacao} {frase_idiomas}"
    ).strip()

    return re.sub(r"\s+", " ", resumo)
