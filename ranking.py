"""
ranking.py
Ordena e classifica os candidatos após o cálculo de score,
e aplica filtros solicitados pela recrutadora.
"""
import pandas as pd
from utils import normalizar_texto


def montar_tabela_resultados(lista_candidatos: list, cidade_vaga: str = "") -> pd.DataFrame:
    cidade_vaga_norm = normalizar_texto(cidade_vaga) if cidade_vaga else ""
    linhas = []
    for c in lista_candidatos:
        idiomas_txt = ", ".join(
            f"{idioma} ({nivel})" for idioma, nivel in c.get("idiomas", {}).items()
        ) or "Não identificado"

        cidade_candidato = c.get("cidade") or ""
        cidade_exibicao = cidade_candidato or "Não identificado"
        fora_da_cidade = False
        alerta_texto = ""
        if cidade_vaga_norm:
            cidade_candidato_norm = normalizar_texto(cidade_candidato)
            if not cidade_candidato_norm:
                fora_da_cidade = True
                alerta_texto = "⚠️ Cidade não identificada"
            elif cidade_candidato_norm != cidade_vaga_norm:
                fora_da_cidade = True
                alerta_texto = "⚠️ Fora da cidade da vaga"

        nivel = c.get("formacao_nivel")
        area = c.get("formacao_area")
        if nivel and area:
            formacao_str = f"{nivel} - {area}"
        elif nivel:
            formacao_str = nivel
        elif area:
            formacao_str = area
        else:
            formacao_str = "Não identificado"

        # Correção #8: competências como texto para o filtro de cargo
        # poder encontrar candidatos mesmo quando o resumo não menciona
        # a competência textualmente.
        competencias_txt = ", ".join(c.get("competencias_encontradas", []))

        linhas.append({
            "Nome": c.get("nome", "Não identificado"),
            "Pontuação": c.get("score_final", 0),
            "Compatibilidade (%)": c.get("compatibilidade_percentual", 0),
            "Experiência": c.get("experiencia_formatada", "Não identificado"),
            "Experiência (meses)": c.get("experiencia_meses", 0),
            "Formação": formacao_str,
            "Idiomas": idiomas_txt,
            "Cidade": cidade_exibicao,
            "Alerta": alerta_texto,
            "Fora da cidade da vaga": fora_da_cidade,
            "Status": c.get("status", "Reprovado"),
            "Email": c.get("email") or "-",
            "Telefone": c.get("telefone") or "-",
            "LinkedIn": c.get("linkedin") or "-",
            "Resumo": c.get("resumo", ""),
            "Competências": competencias_txt,
            "Arquivo": c.get("arquivo", ""),
        })

    df = pd.DataFrame(linhas)
    if not df.empty:
        df = df.sort_values(by="Pontuação", ascending=False).reset_index(drop=True)
        df.insert(0, "Ranking", range(1, len(df) + 1))
    return df


def aplicar_filtros(
    df: pd.DataFrame,
    cidade: str = None,
    experiencia_min_meses: int = None,
    idioma: str = None,
    formacao: str = None,
    pontuacao_minima: float = None,
    cargo: str = None,
) -> pd.DataFrame:
    """Aplica filtros combináveis sobre a tabela de resultados.

    Correção #8: filtro de cargo agora busca também na coluna Competências,
    para encontrar candidatos cujo resumo não mencione a competência
    textualmente mas que ela foi identificada no currículo.

    Correção #15: ranking reordenado após aplicar os filtros, para que
    a numeração seja contínua (1, 2, 3...) e não reflita posições do
    ranking original (ex: 3, 7, 12).
    """
    resultado = df.copy()

    if cidade:
        resultado = resultado[resultado["Cidade"].str.contains(cidade, case=False, na=False)]
    if experiencia_min_meses is not None:
        resultado = resultado[resultado["Experiência (meses)"] >= experiencia_min_meses]
    if idioma:
        resultado = resultado[resultado["Idiomas"].str.contains(idioma, case=False, na=False)]
    if formacao:
        resultado = resultado[resultado["Formação"].str.contains(formacao, case=False, na=False)]
    if pontuacao_minima is not None:
        resultado = resultado[resultado["Pontuação"] >= pontuacao_minima]
    if cargo:
        # Correção #8: busca em Resumo, Nome e Competências
        mask = (
            resultado["Resumo"].str.contains(cargo, case=False, na=False)
            | resultado["Nome"].str.contains(cargo, case=False, na=False)
            | resultado["Competências"].str.contains(cargo, case=False, na=False)
        )
        resultado = resultado[mask]

    # Correção #15: reordenar ranking após filtros
    resultado = resultado.reset_index(drop=True)
    if not resultado.empty and "Ranking" in resultado.columns:
        resultado["Ranking"] = range(1, len(resultado) + 1)

    return resultado
