"""
ranking.py
Ordena e classifica os candidatos após o cálculo de score,
e aplica filtros solicitados pela recrutadora.
"""

import re

import pandas as pd
from utils import normalizar_texto, SIGLAS_ESTADO


def _cidade_base(texto: str) -> str:
    """Normaliza uma cidade e remove a UF: 'Niterói - RJ', 'Niterói/RJ' e
    'Niterói, RJ' viram todas 'niteroi'.

    Correção: o parser devolve 'Cidade - UF' (ex.: 'Niterói - RJ'), mas a
    cidade da vaga costuma ser só 'Niterói'. A comparação era por igualdade
    exata, então TODO candidato que informou a UF recebia o alerta falso de
    'Fora da cidade da vaga'."""
    norm = normalizar_texto(texto or "")
    m = re.search(r"\s*[-/,]\s*([a-z]{2})$", norm)
    if m and m.group(1).upper() in SIGLAS_ESTADO:
        norm = norm[: m.start()]
    return norm.strip()


_NAO_IDENTIFICADO = {"", "nao identificado", "nao informado", "-"}


def _chaves_identidade(c: dict) -> list:
    """Identificadores que apontam para a MESMA pessoa: e-mail, telefone
    (só dígitos, pelo menos 10) e nome completo (2+ palavras)."""
    chaves = []
    email = (c.get("email") or "").strip().lower()
    if email and email != "-":
        chaves.append(("email", email))
    tel = re.sub(r"\D", "", c.get("telefone") or "")
    if len(tel) >= 10:
        chaves.append(("tel", tel[-10:]))
    nome = normalizar_texto(c.get("nome") or "")
    if nome not in _NAO_IDENTIFICADO and len(nome.split()) >= 2:
        chaves.append(("nome", nome))
    return chaves


def _marcar_duplicados(lista_candidatos: list) -> list:
    """Para cada candidato, True se outro da lista compartilha e-mail,
    telefone ou nome. A deduplicação do banco é por ARQUIVO; a mesma pessoa
    que manda duas versões do currículo (ou um PDF e o texto pelo Forms)
    virava dois candidatos sem nenhum aviso."""
    por_chave = {}
    for i, c in enumerate(lista_candidatos):
        for chave in _chaves_identidade(c):
            por_chave.setdefault(chave, set()).add(i)
    duplicado = [False] * len(lista_candidatos)
    for indices in por_chave.values():
        if len(indices) > 1:
            for i in indices:
                duplicado[i] = True
    return duplicado


def montar_tabela_resultados(
    lista_candidatos: list,
    cidade_vaga: str = ""
) -> pd.DataFrame:

    cidade_vaga_norm = _cidade_base(cidade_vaga) if cidade_vaga else ""
    duplicados = _marcar_duplicados(lista_candidatos)

    linhas = []

    for c, eh_duplicado in zip(lista_candidatos, duplicados):

        idiomas_txt = ", ".join(
            f"{idioma} ({nivel})" if nivel else idioma
            for idioma, nivel in (c.get("idiomas") or {}).items()
        ) or "Não identificado"

        cidade_candidato = c.get("cidade") or ""
        cidade_exibicao = cidade_candidato or "Não identificado"

        fora_da_cidade = False
        alerta_texto = ""

        if cidade_vaga_norm:
            cidade_candidato_norm = _cidade_base(cidade_candidato)

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

        # Competências como texto para o filtro de cargo
        competencias_txt = ", ".join(
            c.get("competencias_encontradas") or []
        )

        linhas.append({
            "Nome": c.get("nome") or "Não identificado",
            "Pontuação": c.get("score_final") or 0,
            "Compatibilidade (%)": c.get(
                "compatibilidade_percentual"
            ) or 0,
            "Experiência": c.get(
                "experiencia_formatada"
            ) or "Não identificado",
            "Experiência (meses)": c.get(
                "experiencia_meses"
            ) or 0,
            "Formação": formacao_str,
            "Idiomas": idiomas_txt,
            "Cidade": cidade_exibicao,
            "Alerta": alerta_texto,
            "Fora da cidade da vaga": fora_da_cidade,
            "Duplicidade": "🔁 Possível duplicado (mesmo e-mail, telefone ou nome)" if eh_duplicado else "",
            "Status": c.get("status") or "Reprovado",
            "Email": c.get("email") or "-",
            "Telefone": c.get("telefone") or "-",
            "LinkedIn": c.get("linkedin") or "-",
            "Resumo": c.get("resumo") or "",
            "Cargos": ", ".join(c.get("cargos") or []),
            "Competências": competencias_txt,
            "Arquivo": c.get("arquivo") or "",
            "Hash": c.get("hash_arquivo") or "",
        })

    df = pd.DataFrame(linhas)

    if not df.empty:
        # mergesort é estável: candidatos com a mesma nota mantêm a ordem de
        # entrada, e o ranking não "embaralha" a cada recarga.
        df = df.sort_values(
            by="Pontuação",
            ascending=False,
            kind="mergesort",
        ).reset_index(drop=True)

        df.insert(
            0,
            "Ranking",
            range(1, len(df) + 1)
        )

    return df


def _contem(serie: pd.Series, termo: str) -> pd.Series:
    """Busca literal, sem acento e sem diferenciar maiúsculas. Correção: o
    str.contains antigo tratava o termo como REGEX (digitar 'C++' ou '('
    gerava erro e derrubava a tela) e não achava 'Niteroi' em 'Niterói'."""
    alvo = normalizar_texto(termo)
    return serie.fillna("").map(lambda v: alvo in normalizar_texto(str(v)))


def aplicar_filtros(
    df: pd.DataFrame,
    cidade: str = None,
    experiencia_min_meses: int = None,
    idioma: str = None,
    formacao: str = None,
    pontuacao_minima: float = None,
    cargo: str = None,
) -> pd.DataFrame:
    """
    Aplica filtros combináveis sobre a tabela de resultados.

    O filtro de cargo busca nos Cargos exercidos (extraídos da experiência),
    além de Resumo, Nome e Competências. Currículos analisados antes desta
    versão não têm cargos salvos até serem reenviados.

    O ranking é reordenado após aplicar os filtros.
    """

    resultado = df.copy()

    if cidade:
        resultado = resultado[_contem(resultado["Cidade"], cidade)]

    if experiencia_min_meses is not None:
        resultado = resultado[
            resultado["Experiência (meses)"] >= experiencia_min_meses
        ]

    if idioma:
        resultado = resultado[_contem(resultado["Idiomas"], idioma)]

    if formacao:
        resultado = resultado[_contem(resultado["Formação"], formacao)]

    if pontuacao_minima is not None:
        resultado = resultado[
            resultado["Pontuação"] >= pontuacao_minima
        ]

    if cargo:
        mask = (
            _contem(resultado["Cargos"], cargo)
            | _contem(resultado["Resumo"], cargo)
            | _contem(resultado["Nome"], cargo)
            | _contem(resultado["Competências"], cargo)
        )

        resultado = resultado[mask]

    # Reordenar ranking após aplicar filtros
    resultado = resultado.reset_index(drop=True)

    if not resultado.empty and "Ranking" in resultado.columns:
        resultado["Ranking"] = range(
            1,
            len(resultado) + 1
        )

    return resultado
