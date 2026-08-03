"""
score.py
Calcula a pontuação final de um candidato com base em:
- Palavras obrigatórias (60%)
- Experiência (20%)
- Formação (10%)
- Idiomas (5%)
- Cursos/competências desejáveis (5%)

Se a vaga não tiver palavras-chave obrigatórias e/ou desejáveis definidas,
o peso desses critérios é redistribuído entre os critérios que realmente
têm dado pra avaliar (ver Correção #20 em calcular_score_final).

Também aplica ajuste de aprendizado com base em decisões passadas da
recrutadora (aprovações/reprovações), via database.py.
"""

from keywords import encontrar_ocorrencias

PESOS_CRITERIOS = {
    "obrigatorias": 0.60,
    "experiencia": 0.20,
    "formacao": 0.10,
    "idiomas": 0.05,
    "desejaveis": 0.05,
}

NIVEIS_FORMACAO_PONTOS = {
    "Ensino Fundamental": 20,
    "Ensino Médio": 40,
    "Técnico": 55,
    "Tecnólogo": 65,
    "Graduação": 80,
    "MBA": 90,
    "Pós-graduação": 90,
    "Mestrado": 95,
    "Doutorado": 100,
}

NIVEIS_IDIOMA_PONTOS = {
    "Básico": 30,
    "Intermediário": 60,
    "Avançado": 85,
    "Fluente": 100,
    "Nativo": 100,
    # Correção #6: "Não especificado" explicitamente documentado.
    # Idiomas sem nível identificado recebem 40 pts (entre básico e intermediário).
    "Não especificado": 40,
}

EXPERIENCIA_MESES_REFERENCIA = 60  # 5 anos = pontuação máxima de experiência


def pontuar_palavras(texto: str, palavras_com_peso: dict) -> tuple:
    """
    palavras_com_peso: dict {palavra_chave: peso}
    Retorna (pontuacao_percentual_0_a_100, lista_encontradas, lista_faltantes)

    Nota: quando palavras_com_peso está vazio, retorna 100.0 por
    convenção — mas esse valor só deve ser usado se o critério de fato
    entrar no cálculo do score final. Ver Correção #20 em
    calcular_score_final, que exclui esse critério do peso quando não há
    nenhuma palavra-chave configurada, em vez de deixar esse 100.0
    "de brinde" inflar a nota de todo mundo.
    """
    if not palavras_com_peso:
        return 100.0, [], []

    peso_total = sum(palavras_com_peso.values()) or 1
    peso_obtido = 0
    encontradas, faltantes = [], []

    for palavra, peso in palavras_com_peso.items():
        achou, _ = encontrar_ocorrencias(texto, palavra)
        if achou:
            peso_obtido += peso
            encontradas.append(palavra)
        else:
            faltantes.append(palavra)

    percentual = (peso_obtido / peso_total) * 100
    return round(percentual, 2), encontradas, faltantes


def pontuar_experiencia(meses_experiencia: int) -> float:
    percentual = min(meses_experiencia / EXPERIENCIA_MESES_REFERENCIA, 1.0) * 100
    return round(percentual, 2)


def pontuar_formacao(nivel_formacao: str) -> float:
    if not nivel_formacao:
        return 0.0
    return float(NIVEIS_FORMACAO_PONTOS.get(nivel_formacao, 40))


def pontuar_idiomas(idiomas: dict) -> float:
    """
    Correção #6: proteção explícita contra dict vazio e nível None.
    - dict vazio -> 0.0 (sem idiomas identificados)
    - nível None -> trata como "Não especificado" (40 pts)
    """
    if not idiomas:
        return 0.0
    pontos = [
        NIVEIS_IDIOMA_PONTOS.get(nivel or "Não especificado", 40)
        for nivel in idiomas.values()
    ]
    return round(sum(pontos) / len(pontos), 2)


def calcular_score_final(
    dados_curriculo: dict,
    palavras_obrigatorias: dict,
    palavras_desejaveis: dict,
    ajuste_aprendizado: float = 0.0,
) -> dict:
    """
    Calcula o score final ponderado e retorna o detalhamento completo.
    ajuste_aprendizado: pequeno fator (-10 a +10) aplicado com base no
    histórico de decisões da recrutadora para currículos parecidos.

    Correção #12: usa _texto_bruto_temp (presente apenas durante o
    processamento, não persistido no banco) em vez de texto_bruto.

    Correção #20: antes, quando a vaga não tinha nenhuma palavra-chave
    obrigatória e/ou desejável cadastrada, pontuar_palavras retornava
    100.0 "de brinde" pra esse critério — e como "obrigatorias" sozinho
    vale 60% do score, um candidato com ZERO competências relevantes
    identificadas (ex: 1 ano de experiência, sem nenhuma palavra-chave
    batendo) já saía como "Aprovado" só por a vaga estar mal configurada.
    Agora, se um critério não tem dado nenhum pra avaliar (a recrutadora
    não definiu palavras-chave pra ele), ele é excluído do cálculo e seu
    peso é redistribuído proporcionalmente entre os critérios que
    realmente têm base pra pontuar (experiência, formação, idiomas, e o
    outro grupo de palavras-chave, se houver).
    """
    texto = dados_curriculo.get("_texto_bruto_temp", "")

    score_obrig, encontradas_obrig, faltantes_obrig = pontuar_palavras(texto, palavras_obrigatorias)
    score_desej, encontradas_desej, faltantes_desej = pontuar_palavras(texto, palavras_desejaveis)
    score_exp = pontuar_experiencia(dados_curriculo.get("experiencia_meses", 0))
    score_form = pontuar_formacao(dados_curriculo.get("formacao_nivel"))
    score_idio = pontuar_idiomas(dados_curriculo.get("idiomas", {}))

    scores_por_criterio = {
        "obrigatorias": score_obrig,
        "experiencia": score_exp,
        "formacao": score_form,
        "idiomas": score_idio,
        "desejaveis": score_desej,
    }

    # Correção #20: monta o conjunto de pesos "ativos" (critérios com dado
    # real pra avaliar) e renormaliza para somar 1.0 — se nada foi
    # excluído, o resultado é idêntico ao comportamento original.
    pesos_ativos = dict(PESOS_CRITERIOS)
    obrigatorias_sem_criterio = not palavras_obrigatorias
    desejaveis_sem_criterio = not palavras_desejaveis
    if obrigatorias_sem_criterio:
        pesos_ativos.pop("obrigatorias", None)
    if desejaveis_sem_criterio:
        pesos_ativos.pop("desejaveis", None)

    soma_pesos_ativos = sum(pesos_ativos.values()) or 1.0
    pesos_normalizados = {k: v / soma_pesos_ativos for k, v in pesos_ativos.items()}

    score_final = sum(
        scores_por_criterio[criterio] * peso
        for criterio, peso in pesos_normalizados.items()
    )

    score_final = max(0, min(100, score_final + ajuste_aprendizado))
    score_final = round(score_final, 2)

    if score_final >= 70:
        status = "Aprovado"
    elif score_final >= 50:
        status = "Talvez"
    else:
        status = "Reprovado"

    return {
        "score_final": score_final,
        "compatibilidade_percentual": score_final,
        "status": status,
        "detalhamento": {
            "obrigatorias": {
                "score": score_obrig,
                "encontradas": encontradas_obrig,
                "faltantes": faltantes_obrig,
                "sem_criterio_definido": obrigatorias_sem_criterio,
            },
            "desejaveis": {
                "score": score_desej,
                "encontradas": encontradas_desej,
                "faltantes": faltantes_desej,
                "sem_criterio_definido": desejaveis_sem_criterio,
            },
            "experiencia": {"score": score_exp},
            "formacao": {"score": score_form},
            "idiomas": {"score": score_idio},
        },
    }
