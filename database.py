"""
database.py
Camada de persistência usando PostgreSQL, com isolamento multi-tenant:
cada empresa (cliente que comprou a ferramenta) só enxerga suas próprias
vagas e candidatos, mesmo compartilhando o mesmo banco de dados.

Configuração da conexão (nessa ordem de prioridade):
1. st.secrets["DATABASE_URL"]  (usado no Streamlit Cloud, via secrets.toml)
2. variável de ambiente DATABASE_URL (usado em testes locais)

Responsabilidades:
- Autenticar empresas (via auth.py) e gerenciar contas de clientes.
- Salvar vagas (descrição, palavras-chave, cidade) por empresa.
- Salvar currículos já analisados (evitando reprocessar o mesmo arquivo
  dentro da mesma empresa), vinculados a uma vaga.
- Registrar decisões da recrutadora (Aprovado/Reprovado manual) para
  alimentar o sistema de aprendizado.
"""

import os
import json
from datetime import datetime
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

import auth


def _obter_url_conexao() -> str:
    """Busca a connection string do Postgres nos secrets do Streamlit
    (produção) ou na variável de ambiente (testes locais)."""
    try:
        import streamlit as st
        if "DATABASE_URL" in st.secrets:
            return st.secrets["DATABASE_URL"]
    except Exception:
        pass

    url_ambiente = os.environ.get("DATABASE_URL")
    if url_ambiente:
        return url_ambiente

    raise RuntimeError(
        "Nenhuma DATABASE_URL configurada. Defina em .streamlit/secrets.toml "
        "(produção) ou na variável de ambiente DATABASE_URL (testes locais)."
    )


@contextmanager
def conectar():
    url = _obter_url_conexao()
    conexao = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conexao
        conexao.commit()
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def inicializar_banco():
    with conectar() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS empresas (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                senha_hash TEXT NOT NULL,
                senha_salt TEXT NOT NULL,
                ativo BOOLEAN NOT NULL DEFAULT TRUE,
                data_criacao TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vagas (
                id SERIAL PRIMARY KEY,
                empresa_id INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
                nome TEXT NOT NULL,
                descricao TEXT,
                obrigatorias_json TEXT,
                desejaveis_json TEXT,
                cidade_vaga TEXT,
                data_criacao TIMESTAMP NOT NULL DEFAULT NOW(),
                data_atualizacao TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE (empresa_id, nome)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS curriculos (
                id SERIAL PRIMARY KEY,
                empresa_id INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
                hash_arquivo TEXT NOT NULL,
                nome_arquivo TEXT,
                nome_candidato TEXT,
                email TEXT,
                telefone TEXT,
                cidade TEXT,
                dados_json TEXT NOT NULL,
                score_final REAL,
                status TEXT,
                data_analise TIMESTAMP NOT NULL DEFAULT NOW(),
                vaga_descricao TEXT,
                vaga_id INTEGER REFERENCES vagas(id) ON DELETE SET NULL,
                UNIQUE (empresa_id, hash_arquivo)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS decisoes (
                id SERIAL PRIMARY KEY,
                curriculo_id INTEGER REFERENCES curriculos(id) ON DELETE CASCADE,
                decisao TEXT NOT NULL,
                data_decisao TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.close()


# ---------------------------------------------------------------------------
# Autenticação e gestão de contas (empresas)
# ---------------------------------------------------------------------------

def criar_empresa(nome: str, email: str, senha: str) -> int:
    """Cria a conta de uma nova empresa cliente. Retorna o id criado.
    Levanta ValueError se o e-mail já estiver cadastrado."""
    hash_senha, salt = auth.gerar_hash_senha(senha)
    with conectar() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO empresas (nome, email, senha_hash, senha_salt)
                VALUES (%s, %s, %s, %s) RETURNING id
            """, (nome, email.lower().strip(), hash_senha, salt))
            novo_id = cur.fetchone()["id"]
        except psycopg2.errors.UniqueViolation:
            raise ValueError("Já existe uma empresa cadastrada com esse e-mail.")
        cur.close()
        return novo_id


def autenticar_empresa(email: str, senha: str):
    """Verifica e-mail/senha. Retorna o dict da empresa se válido, ou None."""
    with conectar() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM empresas WHERE email = %s AND ativo = TRUE", (email.lower().strip(),))
        linha = cur.fetchone()
        cur.close()
        if not linha:
            return None
        if auth.verificar_senha(senha, linha["senha_hash"], linha["senha_salt"]):
            return {"id": linha["id"], "nome": linha["nome"], "email": linha["email"]}
        return None


def listar_empresas():
    with conectar() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, nome, email, ativo, data_criacao FROM empresas ORDER BY data_criacao DESC")
        resultado = [dict(l) for l in cur.fetchall()]
        cur.close()
        return resultado


def alterar_status_empresa(empresa_id: int, ativo: bool):
    """Ativa ou desativa (bloqueia) o acesso de uma empresa, sem apagar os dados."""
    with conectar() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE empresas SET ativo = %s WHERE id = %s", (ativo, empresa_id))
        cur.close()


# ---------------------------------------------------------------------------
# Currículos
# ---------------------------------------------------------------------------

def curriculo_ja_analisado(empresa_id: int, hash_arquivo: str):
    with conectar() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM curriculos WHERE empresa_id = %s AND hash_arquivo = %s",
            (empresa_id, hash_arquivo),
        )
        linha = cur.fetchone()
        cur.close()
        if linha:
            dados = json.loads(linha["dados_json"])
            dados["_id_banco"] = linha["id"]
            return dados
        return None


def salvar_curriculo(empresa_id: int, hash_arquivo: str, nome_arquivo: str, dados: dict,
                      vaga_descricao: str = "", vaga_id: int = None):
    """Salva (ou atualiza) o resultado da análise de um currículo, isolado
    por empresa. O mesmo arquivo físico pode existir em empresas diferentes
    sem conflito (cada uma tem seu próprio 'hash_arquivo' com escopo de
    empresa_id)."""
    with conectar() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO curriculos (
                empresa_id, hash_arquivo, nome_arquivo, nome_candidato, email, telefone,
                cidade, dados_json, score_final, status, data_analise,
                vaga_descricao, vaga_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (empresa_id, hash_arquivo) DO UPDATE SET
                dados_json = EXCLUDED.dados_json,
                score_final = EXCLUDED.score_final,
                status = EXCLUDED.status,
                data_analise = EXCLUDED.data_analise,
                vaga_id = COALESCE(EXCLUDED.vaga_id, curriculos.vaga_id)
            RETURNING id
        """, (
            empresa_id, hash_arquivo, nome_arquivo,
            dados.get("nome"), dados.get("email"), dados.get("telefone"), dados.get("cidade"),
            json.dumps(dados, ensure_ascii=False, default=str),
            dados.get("score_final"), dados.get("status"), datetime.now(),
            vaga_descricao, vaga_id,
        ))
        id_banco = cur.fetchone()["id"]
        cur.close()
        return id_banco


def registrar_decisao(curriculo_id: int, decisao: str):
    with conectar() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO decisoes (curriculo_id, decisao, data_decisao) VALUES (%s, %s, %s)",
            (curriculo_id, decisao, datetime.now()),
        )
        cur.close()


def calcular_ajuste_aprendizado(empresa_id: int, competencias_candidato: list) -> float:
    """Sistema de aprendizado simples, calculado apenas com o histórico de
    decisões DA MESMA EMPRESA (não mistura aprendizado entre clientes)."""
    if not competencias_candidato:
        return 0.0

    with conectar() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT c.dados_json, d.decisao
            FROM decisoes d
            JOIN curriculos c ON c.id = d.curriculo_id
            WHERE c.empresa_id = %s
        """, (empresa_id,))
        linhas = cur.fetchall()
        cur.close()

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
        if not (set_competencias_novo & competencias_antigas):
            continue

        total_relevante += 1
        if "aprovado" in linha["decisao"].lower():
            aprovacoes += 1
        elif "reprovado" in linha["decisao"].lower():
            reprovacoes += 1

    if total_relevante == 0:
        return 0.0

    proporcao = (aprovacoes - reprovacoes) / total_relevante
    return round(proporcao * 10, 2)


def listar_curriculos_por_vaga(empresa_id: int, vaga_id: int):
    with conectar() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM curriculos WHERE empresa_id = %s AND vaga_id = %s ORDER BY score_final DESC",
            (empresa_id, vaga_id),
        )
        resultados = []
        for linha in cur.fetchall():
            dados = json.loads(linha["dados_json"])
            dados["_id_banco"] = linha["id"]
            dados["hash_arquivo"] = linha["hash_arquivo"]
            resultados.append(dados)
        cur.close()
        return resultados


# ---------------------------------------------------------------------------
# Vagas
# ---------------------------------------------------------------------------

def salvar_vaga(empresa_id: int, nome: str, descricao: str, palavras_obrigatorias: dict,
                 palavras_desejaveis: dict, cidade_vaga: str = "") -> int:
    agora = datetime.now()
    with conectar() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO vagas (empresa_id, nome, descricao, obrigatorias_json, desejaveis_json, cidade_vaga, data_criacao, data_atualizacao)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (empresa_id, nome) DO UPDATE SET
                descricao = EXCLUDED.descricao,
                obrigatorias_json = EXCLUDED.obrigatorias_json,
                desejaveis_json = EXCLUDED.desejaveis_json,
                cidade_vaga = EXCLUDED.cidade_vaga,
                data_atualizacao = EXCLUDED.data_atualizacao
            RETURNING id
        """, (
            empresa_id, nome, descricao,
            json.dumps(palavras_obrigatorias, ensure_ascii=False),
            json.dumps(palavras_desejaveis, ensure_ascii=False),
            cidade_vaga or "", agora, agora,
        ))
        novo_id = cur.fetchone()["id"]
        cur.close()
        return novo_id


def listar_vagas(empresa_id: int):
    with conectar() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM vagas WHERE empresa_id = %s ORDER BY data_atualizacao DESC", (empresa_id,)
        )
        resultado = [dict(l) for l in cur.fetchall()]
        cur.close()
        return resultado


def obter_vaga(empresa_id: int, vaga_id: int):
    with conectar() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM vagas WHERE id = %s AND empresa_id = %s", (vaga_id, empresa_id))
        linha = cur.fetchone()
        cur.close()
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


def excluir_vaga(empresa_id: int, vaga_id: int):
    with conectar() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE curriculos SET vaga_id = NULL WHERE vaga_id = %s AND empresa_id = %s", (vaga_id, empresa_id))
        cur.execute("DELETE FROM vagas WHERE id = %s AND empresa_id = %s", (vaga_id, empresa_id))
        cur.close()


def limpar_banco():
    """Apaga TODOS os dados de TODAS as empresas. Uso apenas em testes."""
    with conectar() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM decisoes")
        cur.execute("DELETE FROM curriculos")
        cur.execute("DELETE FROM vagas")
        cur.execute("DELETE FROM empresas")
        cur.close()
