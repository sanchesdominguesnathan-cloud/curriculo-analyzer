"""
keywords.py
Gerencia o dicionário de palavras-chave, pesos e sinônimos.
Permite que a recrutadora cadastre novas palavras-chave com pesos
personalizados diretamente pela interface (armazenado no banco de dados).

O reconhecimento é feito por:
1. Expansão via base de sinônimos (dicionário extenso pré-carregado).
2. Correspondência aproximada (fuzzy matching) via RapidFuzz, para lidar
   com pequenas variações ortográficas.
3. Busca por contexto: expressões diferentes que significam a mesma coisa
   (ex.: "domínio avançado de planilhas" -> Excel).
"""

import re
from functools import lru_cache

from rapidfuzz import fuzz
from utils import normalizar_texto

# ---------------------------------------------------------------------------
# Base de sinônimos. Cada chave é a "palavra-chave canônica" e o valor é uma
# lista de variações/sinônimos/expressões de contexto que devem contar como
# a palavra-chave correspondente.
# ---------------------------------------------------------------------------
BASE_SINONIMOS = {
    "python": ["python", "py", "linguagem python"],
    "excel": [
        "excel", "microsoft excel", "planilhas", "planilha eletronica",
        "office", "pacote office", "dominio de planilhas", "avancado em excel",
        "tabelas dinamicas", "dashboards em excel"
    ],
    "power bi": ["power bi", "powerbi", "microsoft power bi", "bi", "business intelligence"],
    "sql": ["sql", "mysql", "postgresql", "sql server", "banco de dados", "consultas sql", "queries"],
    "sap": ["sap", "sap erp", "sap fi", "sap mm", "sap sd", "sap hcm"],
    "erp": ["erp", "sistema erp", "enterprise resource planning", "totvs", "protheus", "senior sistemas"],
    "rh": [
        "rh", "recursos humanos", "human resources", "people", "departamento pessoal",
        "gestao de pessoas", "people analytics", "hr business partner", "hrbp"
    ],
    "financeiro": [
        "financeiro", "finance", "financial", "financas", "tesouraria", "contas a pagar",
        "contas a receber", "controladoria", "fluxo de caixa", "contabilidade"
    ],
    "marketing": [
        "marketing", "marketing digital", "trafego pago", "midias sociais", "growth",
        "branding", "publicidade", "propaganda", "inbound marketing"
    ],
    "vendas": [
        "vendas", "comercial", "sales", "negociacao", "prospeccao", "pos venda",
        "account manager", "representante comercial", "closer"
    ],
    "administracao": [
        "administracao", "administrador", "administrativo", "administrative",
        "business administration", "gestao administrativa", "rotinas administrativas"
    ],
    "gestao": [
        "gestao", "gestor", "management", "gerenciamento", "coordenacao",
        "lideranca de equipe", "supervisao"
    ],
    "logistica": [
        "logistica", "logistics", "supply chain", "cadeia de suprimentos",
        "armazenagem", "estoque", "expedicao", "transporte"
    ],
    "compras": ["compras", "purchasing", "procurement", "suprimentos", "cotacao", "fornecedores"],
    "planejamento": [
        "planejamento", "planning", "pcp", "planejamento estrategico",
        "planejamento e controle da producao", "orcamento", "budget"
    ],
    "analise de dados": [
        "analise de dados", "data analysis", "analista de dados", "data analytics",
        "business analytics", "power query", "visualizacao de dados"
    ],
    "atendimento": [
        "atendimento", "atendimento ao cliente", "customer service", "sac",
        "suporte ao cliente", "relacionamento com cliente"
    ],
    "pacote office": [
        "pacote office", "office", "microsoft office", "word", "powerpoint", "outlook"
    ],
    "comunicacao": ["comunicacao", "communication", "comunicacao assertiva", "oratoria"],
    "lideranca": [
        "lideranca", "leadership", "gestao de equipes", "lider de equipe",
        "team leader", "supervisor de equipe"
    ],
    "organizacao": ["organizacao", "organizacional", "organized", "organizado"],
    "ingles": ["ingles", "english", "inglês avancado", "fluente em ingles"],
    "espanhol": ["espanhol", "spanish", "espanol"],
}

# Pesos padrão sugeridos (a recrutadora pode sobrescrever pela interface)
PESOS_PADRAO = {
    "python": 10,
    "excel": 8,
    "power bi": 10,
    "sql": 9,
    "sap": 9,
    "erp": 7,
    "rh": 6,
    "financeiro": 6,
    "marketing": 6,
    "vendas": 6,
    "administracao": 7,
    "gestao": 7,
    "logistica": 6,
    "compras": 6,
    "planejamento": 6,
    "analise de dados": 9,
    "atendimento": 5,
    "pacote office": 5,
    "comunicacao": 5,
    "lideranca": 5,
    "organizacao": 4,
    "ingles": 6,
    "espanhol": 4,
}


def obter_variacoes(palavra_chave: str):
    """Retorna a lista de variações/sinônimos para uma palavra-chave.
    Se a palavra não estiver na base, retorna apenas ela mesma normalizada."""
    chave_norm = normalizar_texto(palavra_chave)
    if chave_norm in BASE_SINONIMOS:
        return [normalizar_texto(v) for v in BASE_SINONIMOS[chave_norm]]
    return [chave_norm]


@lru_cache(maxsize=1024)
def _padrao_variacao(variacao: str):
    """Regex com FRONTEIRA DE PALAVRA para uma variação já normalizada.

    Correção: a busca antiga era por substring pura (`variacao in texto`), o
    que gerava falsos positivos em massa — "excel" dentro de "excelente",
    "bi" dentro de "habilidades", "erp" dentro de "interpretação", "py" dentro
    de "copy", "sap" dentro de "sapato", "word" dentro de "keyword". Como as
    palavras obrigatórias valem 60% da nota, praticamente todo currículo
    "tinha" Excel e Power BI. Agora a variação não pode estar colada em
    letras/números; um plural simples (s/es) é aceito ("planilhas", "vendas").
    """
    return re.compile(r"(?<![a-z0-9])" + re.escape(variacao) + r"(?:s|es)?(?![a-z0-9])")


# Variações curtas (ex.: "bi", "sap", "py") só valem por correspondência exata;
# no fuzzy elas casariam com qualquer palavra parecida.
_TAMANHO_MINIMO_FUZZY = 5


def encontrar_ocorrencias(texto_curriculo: str, palavra_chave: str, limiar_fuzzy: int = 87):
    """
    Verifica se uma palavra-chave (ou qualquer de seus sinônimos/variações de
    contexto) aparece no texto do currículo.

    Retorna (encontrado: bool, trecho_evidencia: str | None)
    """
    texto_norm = normalizar_texto(texto_curriculo)
    variacoes = obter_variacoes(palavra_chave)

    # 1) Correspondência direta, como palavra inteira - mais confiável
    for variacao in variacoes:
        if not variacao:
            continue
        m = _padrao_variacao(variacao).search(texto_norm)
        if m:
            # O trecho de evidência sai do texto NORMALIZADO, o mesmo onde o
            # índice foi calculado (antes o índice vinha do texto normalizado
            # mas o corte era feito no texto original, desalinhando o trecho).
            inicio = max(0, m.start() - 30)
            fim = min(len(texto_norm), m.end() + 30)
            return True, texto_norm[inicio:fim].strip()

    # 2) Correspondência aproximada (fuzzy) palavra a palavra, para pegar
    # pequenas variações ortográficas não previstas na base de sinônimos
    palavras_texto = texto_norm.split()
    for variacao in variacoes:
        if len(variacao) < _TAMANHO_MINIMO_FUZZY:
            continue
        n_palavras = len(variacao.split())
        if n_palavras == 1:
            for palavra in palavras_texto:
                if fuzz.ratio(palavra, variacao) >= limiar_fuzzy:
                    return True, f"(similaridade aproximada com '{variacao}')"
        else:
            # janela deslizante para expressões de múltiplas palavras
            for i in range(len(palavras_texto) - n_palavras + 1):
                janela = " ".join(palavras_texto[i:i + n_palavras])
                if fuzz.ratio(janela, variacao) >= limiar_fuzzy:
                    return True, f"(similaridade aproximada com '{variacao}')"

    return False, None


def listar_todas_palavras_chave_sugeridas():
    """Retorna a lista de palavras-chave conhecidas na base, para preencher
    sugestões na interface."""
    return list(BASE_SINONIMOS.keys())


def montar_lista_palavras(pesos_obrigatorias: dict, pesos_desejaveis: dict) -> list:
    """Junta obrigatórias + desejáveis numa lista única, SEM repetição e em
    ordem DETERMINÍSTICA (obrigatórias primeiro, de maior para menor peso).

    Correção: o código antigo usava `list(set(...))`. A ordem de um `set` de
    strings muda a cada execução do Python (hash randomization), então as
    "Principais competências" do resumo — que pegam as 4 primeiras — mudavam
    de ordem entre reinícios do app e entre candidatos reprocessados."""
    vistos, resultado = set(), []
    for grupo in (pesos_obrigatorias or {}, pesos_desejaveis or {}):
        ordenadas = sorted(grupo.items(), key=lambda kv: (-(kv[1] or 0), kv[0].lower()))
        for palavra, _peso in ordenadas:
            chave = palavra.lower().strip()
            if chave and chave not in vistos:
                vistos.add(chave)
                resultado.append(palavra)
    return resultado
