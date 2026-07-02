"""
database.py
Camada de persistência usando SQLite (arquivo local, zero configuração).

Responsabilidades:
- Salvar todos os currículos já analisados (evitando reprocessar o mesmo
  arquivo, identificado por hash SHA-256 do conteúdo).
- Registrar decisões da recrutadora (Aprovado/Talvez/Reprovado ou override
  manual) para alimentar o "sistema de aprendizado" simples.
- Guardar palavras-chave cadastradas e seus pesos, para reaproveitar entre
  sessões.
"""

import sqlite3
import json
from datetime import datetime
from contextlib import contextmanager

CAMINHO_BANCO = "data/curriculos.db"


@contextmanager
def conectar():
    conexao = sqlite3.connect(CAMINHO_BANCO)
    conexao.row_factory = sqlite3.Row
    try:
        yield conexao
        conexao.commit()
    finally:
        conexao.close()


def inicializar_banco():
    with conectar() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS curriculos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hash_arquivo TEXT UNIQUE NOT NULL,
                nome_arquivo TEXT,
                nome_candidato TEXT,
                email TEXT,
                telefone TEXT,
                cidade TEXT,
                dados_json TEXT NOT NULL,
                score_final REAL,
                status TEXT,
                data_analise TEXT,
                vaga_descricao TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS decisoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                curriculo_id INTEGER,
                decisao TEXT NOT NULL,
                data_decisao TEXT,
                FOREIGN KEY (curriculo_id) REFERENCES curriculos (id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS palavras_chave (
                palavra TEXT PRIMARY KEY,
                peso INTEGER NOT NULL,
                tipo TEXT NOT NULL DEFAULT 'obrigatoria'
            )
        """)


def curriculo_ja_analisado(hash_arquivo: str):
    with conectar() as conn:
        cursor = conn.execute(
            "SELECT * FROM curriculos WHERE hash_arquivo = ?", (hash_arquivo,)
        )
        linha = cursor.fetchone()
        if linha:
            dados = json.loads(linha["dados_json"])
            dados["_id_banco"] = linha["id"]
            return dados
        return None


def salvar_curriculo(hash_arquivo: str, nome_arquivo: str, dados: dict, vaga_descricao: str = ""):
    """Salva (ou atualiza) o resultado da análise de um currículo."""
    with conectar() as conn:
        conn.execute("""
            INSERT INTO curriculos (
                hash_arquivo, nome_arquivo, nome_candidato, email, telefone,
                cidade, dados_json, score_final, status, data_analise, vaga_descricao
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(hash_arquivo) DO UPDATE SET
                dados_json=excluded.dados_json,
                score_final=excluded.score_final,
                status=excluded.status,
                data_analise=excluded.data_analise
        """, (
            hash_arquivo,
            nome_arquivo,
            dados.get("nome"),
            dados.get("email"),
            dados.get("telefone"),
            dados.get("cidade"),
            json.dumps(dados, ensure_ascii=False, default=str),
            dados.get("score_final"),
            dados.get("status"),
            datetime.now().isoformat(),
            vaga_descricao,
        ))
        cursor = conn.execute(
            "SELECT id FROM curriculos WHERE hash_arquivo = ?", (hash_arquivo,)
        )
        return cursor.fetchone()["id"]


def registrar_decisao(curriculo_id: int, decisao: str):
    """Registra a decisão manual da recrutadora (ex: 'Aprovado manualmente',
    'Reprovado manualmente') para retroalimentar o algoritmo no futuro."""
    with conectar() as conn:
        conn.execute("""
            INSERT INTO decisoes (curriculo_id, decisao, data_decisao)
            VALUES (?, ?, ?)
        """, (curriculo_id, decisao, datetime.now().isoformat()))


def calcular_ajuste_aprendizado(competencias_candidato: list) -> float:
    """
    Sistema de aprendizado simples: analisa decisões passadas de candidatos
    com competências parecidas e calcula um pequeno ajuste (-10 a +10) para
    o score do novo candidato.

    Quanto mais aprovações históricas entre candidatos com competências em
    comum, maior o ajuste positivo (e vice-versa para reprovações).
    """
    if not competencias_candidato:
        return 0.0

    with conectar() as conn:
        cursor = conn.execute("""
            SELECT c.dados_json, d.decisao
            FROM decisoes d
            JOIN curriculos c ON c.id = d.curriculo_id
        """)
        linhas = cursor.fetchall()

    if not linhas:
        return 0.0

    aprovacoes, reprovacoes, total_relevante = 0, 0, 0
    set_competencias_novo = set(competencias_candidato)

    for linha in linhas:
        try:
            dados_antigos = json.loads(linha["dados_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        competencias_antigas = set(dados_antigos.get("competencias_encontradas", []))
        intersecao = set_competencias_novo & competencias_antigas
        if not intersecao:
            continue

        total_relevante += 1
        if "aprovado" in linha["decisao"].lower():
            aprovacoes += 1
        elif "reprovado" in linha["decisao"].lower():
            reprovacoes += 1

    if total_relevante == 0:
        return 0.0

    proporcao = (aprovacoes - reprovacoes) / total_relevante
    ajuste = proporcao * 10  # escala para no máximo +-10 pontos
    return round(ajuste, 2)


def listar_todos_curriculos():
    with conectar() as conn:
        cursor = conn.execute("SELECT * FROM curriculos ORDER BY score_final DESC")
        return [dict(linha) for linha in cursor.fetchall()]


def salvar_palavra_chave(palavra: str, peso: int, tipo: str = "obrigatoria"):
    with conectar() as conn:
        conn.execute("""
            INSERT INTO palavras_chave (palavra, peso, tipo)
            VALUES (?, ?, ?)
            ON CONFLICT(palavra) DO UPDATE SET peso=excluded.peso, tipo=excluded.tipo
        """, (palavra, peso, tipo))


def listar_palavras_chave(tipo: str = None):
    with conectar() as conn:
        if tipo:
            cursor = conn.execute("SELECT * FROM palavras_chave WHERE tipo = ?", (tipo,))
        else:
            cursor = conn.execute("SELECT * FROM palavras_chave")
        return {linha["palavra"]: linha["peso"] for linha in cursor.fetchall()}


def limpar_banco():
    """Apaga todos os dados (útil para testes)."""
    with conectar() as conn:
        conn.execute("DELETE FROM decisoes")
        conn.execute("DELETE FROM curriculos")
        conn.execute("DELETE FROM palavras_chave")
