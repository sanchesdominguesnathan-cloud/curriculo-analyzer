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

import os
import tempfile
import sqlite3
import json
from datetime import datetime
from contextlib import contextmanager

# No Streamlit Community Cloud, a pasta do repositório é somente leitura.
# Por isso usamos uma pasta gravável do sistema (tempfile.gettempdir()),
# que funciona tanto localmente quanto em nuvem.
# ATENÇÃO: em serviços como o Streamlit Community Cloud, essa pasta é
# temporária — os dados podem ser apagados quando o app reiniciar/dormir.
# Para persistência permanente em produção, considere um banco externo
# (ex: PostgreSQL no Supabase ou Neon).
PASTA_DADOS = os.path.join(tempfile.gettempdir(), "curriculo_analyzer_data")
CAMINHO_BANCO = os.path.join(PASTA_DADOS, "curriculos.db")

os.makedirs(PASTA_DADOS, exist_ok=True)


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
            CREATE TABLE IF NOT EXISTS vagas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT UNIQUE NOT NULL,
                descricao TEXT,
                obrigatorias_json TEXT,
                desejaveis_json TEXT,
                cidade_vaga TEXT,
                data_criacao TEXT,
                data_atualizacao TEXT
            )
        """)
        # Migração leve: garante a coluna cidade_vaga em bancos já existentes.
        colunas_vagas = [c["name"] for c in conn.execute("PRAGMA table_info(vagas)").fetchall()]
        if "cidade_vaga" not in colunas_vagas:
            conn.execute("ALTER TABLE vagas ADD COLUMN cidade_vaga TEXT")
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
                vaga_descricao TEXT,
                vaga_id INTEGER,
                FOREIGN KEY (vaga_id) REFERENCES vagas (id)
            )
        """)
        # Migração leve: garante que a coluna vaga_id exista mesmo em bancos
        # criados por uma versão anterior do sistema (sem essa coluna).
        colunas_existentes = [c["name"] for c in conn.execute("PRAGMA table_info(curriculos)").fetchall()]
        if "vaga_id" not in colunas_existentes:
            conn.execute("ALTER TABLE curriculos ADD COLUMN vaga_id INTEGER")

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


def salvar_curriculo(hash_arquivo: str, nome_arquivo: str, dados: dict, vaga_descricao: str = "", vaga_id: int = None):
    """Salva (ou atualiza) o resultado da análise de um currículo, associado
    a uma vaga (opcional)."""
    with conectar() as conn:
        conn.execute("""
            INSERT INTO curriculos (
                hash_arquivo, nome_arquivo, nome_candidato, email, telefone,
                cidade, dados_json, score_final, status, data_analise,
                vaga_descricao, vaga_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(hash_arquivo) DO UPDATE SET
                dados_json=excluded.dados_json,
                score_final=excluded.score_final,
                status=excluded.status,
                data_analise=excluded.data_analise,
                vaga_id=COALESCE(excluded.vaga_id, curriculos.vaga_id)
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
            vaga_id,
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


def listar_curriculos_por_vaga(vaga_id: int):
    """Retorna os dados completos (já decodificados) de todos os currículos
    já analisados anteriormente para uma vaga específica — usado para
    continuar a análise em outro dia sem perder o que já foi feito."""
    with conectar() as conn:
        cursor = conn.execute(
            "SELECT * FROM curriculos WHERE vaga_id = ? ORDER BY score_final DESC", (vaga_id,)
        )
        resultados = []
        for linha in cursor.fetchall():
            dados = json.loads(linha["dados_json"])
            dados["_id_banco"] = linha["id"]
            dados["hash_arquivo"] = linha["hash_arquivo"]
            resultados.append(dados)
        return resultados


# ---------------------------------------------------------------------------
# Gerenciamento de Vagas
# ---------------------------------------------------------------------------

def salvar_vaga(nome: str, descricao: str, palavras_obrigatorias: dict, palavras_desejaveis: dict, cidade_vaga: str = ""):
    """Cria uma nova vaga ou atualiza uma já existente com o mesmo nome.
    Retorna o id da vaga."""
    agora = datetime.now().isoformat()
    with conectar() as conn:
        conn.execute("""
            INSERT INTO vagas (nome, descricao, obrigatorias_json, desejaveis_json, cidade_vaga, data_criacao, data_atualizacao)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(nome) DO UPDATE SET
                descricao=excluded.descricao,
                obrigatorias_json=excluded.obrigatorias_json,
                desejaveis_json=excluded.desejaveis_json,
                cidade_vaga=excluded.cidade_vaga,
                data_atualizacao=excluded.data_atualizacao
        """, (
            nome,
            descricao,
            json.dumps(palavras_obrigatorias, ensure_ascii=False),
            json.dumps(palavras_desejaveis, ensure_ascii=False),
            cidade_vaga or "",
            agora,
            agora,
        ))
        cursor = conn.execute("SELECT id FROM vagas WHERE nome = ?", (nome,))
        return cursor.fetchone()["id"]


def listar_vagas():
    """Retorna a lista de vagas salvas, mais recentes primeiro."""
    with conectar() as conn:
        cursor = conn.execute("SELECT * FROM vagas ORDER BY data_atualizacao DESC")
        return [dict(linha) for linha in cursor.fetchall()]


def obter_vaga(vaga_id: int):
    """Retorna os dados completos de uma vaga (descrição + palavras-chave com pesos + cidade)."""
    with conectar() as conn:
        cursor = conn.execute("SELECT * FROM vagas WHERE id = ?", (vaga_id,))
        linha = cursor.fetchone()
        if not linha:
            return None
        return {
            "id": linha["id"],
            "nome": linha["nome"],
            "descricao": linha["descricao"] or "",
            "obrigatorias": json.loads(linha["obrigatorias_json"] or "{}"),
            "desejaveis": json.loads(linha["desejaveis_json"] or "{}"),
            "cidade_vaga": linha["cidade_vaga"] or "",
        }


def excluir_vaga(vaga_id: int):
    """Exclui uma vaga. Os currículos já analisados permanecem no banco,
    apenas desvinculados da vaga."""
    with conectar() as conn:
        conn.execute("UPDATE curriculos SET vaga_id = NULL WHERE vaga_id = ?", (vaga_id,))
        conn.execute("DELETE FROM vagas WHERE id = ?", (vaga_id,))


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
        conn.execute("DELETE FROM vagas")
