"""
database.py
Camada de persistência usando PostgreSQL, com isolamento multi-tenant:
cada empresa (cliente que comprou a ferramenta) só enxerga suas próprias
vagas e candidatos, mesmo compartilhando o mesmo banco de dados.

Configuração da conexão (nessa ordem de prioridade):
1. variável de ambiente DATABASE_URL (usado no Render, e também em testes locais)
2. st.secrets["DATABASE_URL"]  (usado no Streamlit Cloud, via secrets.toml)

Responsabilidades:
- Autenticar empresas (via auth.py) e gerenciar contas de clientes.
- Salvar vagas (descrição, palavras-chave, cidade) por empresa.
- Salvar currículos já analisados (evitando reprocessar o mesmo arquivo
  dentro da mesma empresa), vinculados a uma vaga.
- Registrar decisões da recrutadora (Aprovado/Talvez/Reprovado manual),
  tanto para alimentar o sistema de aprendizado quanto para sobrepor o
  status exibido do candidato.
"""

import os
import json
from datetime import datetime
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

import auth


def _obter_url_conexao() -> str:
    """Busca a connection string do Postgres.

    Prioridade:
    1. Variável de ambiente DATABASE_URL — é assim que o Render (e testes
       locais) fornecem o segredo. O Render NÃO usa secrets.toml, então
       essa checagem precisa vir primeiro.
    2. st.secrets["DATABASE_URL"] — usado quando o app roda no Streamlit
       Cloud, que injeta os segredos via arquivo secrets.toml.

    Correção: antes só olhava st.secrets primeiro, o que quebrava no
    Render com o erro "No secrets found" mesmo com DATABASE_URL já
    configurada certinho nas Environment Variables do Render.
    """
    url_ambiente = os.environ.get("DATABASE_URL")
    if url_ambiente:
        return url_ambiente

    try:
        import streamlit as st
        if "DATABASE_URL" in st.secrets:
            return st.secrets["DATABASE_URL"]
    except Exception:
        pass

    raise RuntimeError(
        "Nenhuma DATABASE_URL configurada. No Render, defina em "
        "Environment Variables. No Streamlit Cloud, defina em "
        ".streamlit/secrets.toml. Em testes locais, defina a variável "
        "de ambiente DATABASE_URL."
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
                vaga_id INTEGER REFERENCES vagas(id) ON DELETE SET NULL
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

        # Correção #19: coluna nova para guardar a decisão manual mais
        # recente do recrutador (Aprovado/Talvez/Reprovado), separada do
        # status automático calculado pelo score. Antes, clicar em
        # Aprovar/Reprovar só gravava um registro na tabela `decisoes`
        # (usada apenas pelo sistema de aprendizado) e não tinha efeito
        # nenhum visível na tabela nem no dashboard — o clique parecia não
        # fazer nada. ADD COLUMN IF NOT EXISTS é seguro de rodar em bancos
        # que já existem em produção, sem precisar de migração manual.
        cur.execute("""
            ALTER TABLE curriculos ADD COLUMN IF NOT EXISTS decisao_manual TEXT
        """)

        # Correção (candidatos "pulando"): a unicidade era (empresa_id,
        # hash_arquivo), ou seja, UMA linha por arquivo na empresa inteira.
        # Ao analisar o mesmo currículo para uma segunda vaga, o ON CONFLICT
        # sobrescrevia a linha e MOVIA o candidato para a vaga nova — ele
        # sumia da primeira, com a nota da segunda. Agora a unicidade é por
        # (empresa, vaga, arquivo): o mesmo currículo pode concorrer a várias
        # vagas, uma linha em cada, e dentro de uma vaga nunca duplica.
        # COALESCE(vaga_id, 0) faz os currículos sem vaga também serem únicos
        # (NULL é sempre "diferente" de NULL num índice único comum).
        # Idempotente: seguro de rodar em bancos que já existem em produção.
        cur.execute("""
            ALTER TABLE curriculos
            DROP CONSTRAINT IF EXISTS curriculos_empresa_id_hash_arquivo_key
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_curriculos_empresa_vaga_hash
                ON curriculos (empresa_id, (COALESCE(vaga_id, 0)), hash_arquivo)
        """)

        # Correção #18: índices para as queries de busca mais frequentes.
        # Sem eles, cada consulta faz seq scan na tabela inteira.
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_curriculos_empresa_hash
                ON curriculos(empresa_id, hash_arquivo)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_curriculos_vaga
                ON curriculos(empresa_id, vaga_id)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_decisoes_curriculo
                ON decisoes(curriculo_id)
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

def _aplicar_decisao_manual(dados: dict, decisao_manual):
    """Se o recrutador já tomou uma decisão manual (Aprovado/Talvez/Reprovado)
    para esse candidato, ela passa a valer como o status "oficial" exibido
    na tabela e no dashboard — sobrepondo o status automático calculado
    pelo score. O status automático original fica preservado em
    'status_automatico', caso seja útil de consultar depois."""
    dados["status_automatico"] = dados.get("status")
    if decisao_manual:
        dados["status"] = decisao_manual
    dados["decisao_manual"] = decisao_manual
    return dados


def curriculo_ja_analisado(empresa_id: int, hash_arquivo: str, vaga_id: int = None):
    """Busca a análise já salva deste arquivo NESTA vaga (o mesmo arquivo
    pode ter uma análise diferente em cada vaga)."""
    with conectar() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM curriculos WHERE empresa_id = %s AND hash_arquivo = %s "
            "AND vaga_id IS NOT DISTINCT FROM %s",
            (empresa_id, hash_arquivo, vaga_id),
        )
        linha = cur.fetchone()
        cur.close()
        if linha:
            # Correção #2: tratamento explícito de JSON corrompido no banco,
            # em vez de deixar explodir com stack trace para o usuário.
            try:
                dados = json.loads(linha["dados_json"])
            except (json.JSONDecodeError, TypeError):
                return None
            dados["_id_banco"] = linha["id"]
            # Correção #4: não reanexar conteudo_bytes à sessão ao recuperar
            # do banco — esses bytes já foram processados e não precisam
            # ficar acumulados na memória da sessão.
            dados.pop("conteudo_bytes", None)
            dados = _aplicar_decisao_manual(dados, linha.get("decisao_manual"))
            return dados
        return None


def salvar_curriculo(empresa_id: int, hash_arquivo: str, nome_arquivo: str, dados: dict,
                      vaga_descricao: str = "", vaga_id: int = None):
    """Salva (ou atualiza) o resultado da análise de um currículo, isolado
    por empresa. O mesmo arquivo físico pode existir em empresas diferentes
    sem conflito (cada uma tem seu próprio 'hash_arquivo' com escopo de
    empresa_id).

    Correção #12 / #20: campos transitórios são excluídos antes de
    serializar para JSON.

    Bug real corrigido aqui: o filtro antigo procurava por "texto_bruto",
    mas o nome de fato usado no resto do código sempre foi
    "_texto_bruto_temp" — ou seja, o filtro nunca excluía nada, e o texto
    integral de cada currículo (nome, CPF, endereço, telefone...) estava
    sendo salvo permanentemente no banco. Além disso, "_bytes_download"
    (os bytes binários do PDF/DOCX original) também não estava na lista
    de exclusão, e — como json.dumps usa default=str para campos não
    serializáveis — os bytes do arquivo inteiro viravam uma string
    ilegível dentro de dados_json, inchando o banco desnecessariamente.

    A checagem fica centralizada AQUI (e não em quem chama essa função)
    de propósito: mesmo que outro ponto do código no futuro esqueça de dar
    pop() nesses campos antes de chamar salvar_curriculo, eles nunca vão
    parar no banco.
    """
    CAMPOS_NAO_PERSISTIDOS = {
        "texto_bruto", "conteudo_bytes",   # nomes legados/alternativos
        "_texto_bruto_temp",   # texto bruto do currículo — pode conter PII sensível
        "_bytes_download",     # bytes binários do arquivo original — nunca deve virar JSON
        "_id_banco",           # é o próprio id da linha; redundante persistir dentro do JSON
    }
    dados_para_salvar = {
        k: v for k, v in dados.items()
        if k not in CAMPOS_NAO_PERSISTIDOS
    }

    with conectar() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO curriculos (
                empresa_id, hash_arquivo, nome_arquivo, nome_candidato, email, telefone,
                cidade, dados_json, score_final, status, data_analise,
                vaga_descricao, vaga_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (empresa_id, (COALESCE(vaga_id, 0)), hash_arquivo) DO UPDATE SET
                nome_arquivo = EXCLUDED.nome_arquivo,
                nome_candidato = EXCLUDED.nome_candidato,
                email = EXCLUDED.email,
                telefone = EXCLUDED.telefone,
                cidade = EXCLUDED.cidade,
                dados_json = EXCLUDED.dados_json,
                score_final = EXCLUDED.score_final,
                status = EXCLUDED.status,
                data_analise = EXCLUDED.data_analise,
                vaga_descricao = EXCLUDED.vaga_descricao
            RETURNING id
        """, (
            empresa_id, hash_arquivo, nome_arquivo,
            dados.get("nome"), dados.get("email"), dados.get("telefone"), dados.get("cidade"),
            json.dumps(dados_para_salvar, ensure_ascii=False, default=str),
            dados.get("score_final"), dados.get("status"), datetime.now(),
            vaga_descricao, vaga_id,
        ))
        id_banco = cur.fetchone()["id"]
        cur.close()
        return id_banco


DECISOES_VALIDAS = {"Aprovado", "Talvez", "Reprovado"}


def registrar_decisao(empresa_id: int, curriculo_id: int, decisao: str):
    """Registra a decisão manual do recrutador sobre um candidato
    (espera-se "Aprovado", "Talvez" ou "Reprovado").

    Correção #19: antes, essa função só inseria um registro na tabela
    `decisoes` — histórico usado exclusivamente por
    calcular_ajuste_aprendizado (sistema de aprendizado) — mas nunca
    atualizava o candidato em si. Por isso, clicar em Aprovar/Reprovar na
    interface não tinha nenhum efeito visível: a tabela e o dashboard
    continuavam mostrando o status calculado automaticamente pelo score,
    dando a impressão de que o botão não fazia nada (mesmo a decisão
    estando, de fato, salva no banco).

    Agora, além de manter o histórico em `decisoes` (necessário para o
    aprendizado continuar funcionando com múltiplas decisões ao longo do
    tempo), também atualizamos `curriculos.decisao_manual` com a decisão
    mais recente — essa coluna passa a valer como o status "oficial" do
    candidato, sobrepondo o automático, tanto na tabela quanto no
    dashboard, e persistindo entre sessões."""
    if decisao not in DECISOES_VALIDAS:
        raise ValueError(f"Decisão inválida: {decisao!r}.")

    with conectar() as conn:
        cur = conn.cursor()
        # Correção (segurança multi-tenant): o UPDATE só vale se o currículo
        # pertencer à empresa informada. Antes a função recebia só o id, e
        # qualquer id (sequencial, fácil de adivinhar) alterava o currículo
        # de OUTRA empresa e ainda contaminava o aprendizado dela.
        cur.execute(
            "UPDATE curriculos SET decisao_manual = %s WHERE id = %s AND empresa_id = %s RETURNING id",
            (decisao, curriculo_id, empresa_id),
        )
        if cur.fetchone() is None:
            cur.close()
            raise PermissionError("Currículo não encontrado para esta empresa.")
        cur.execute(
            "INSERT INTO decisoes (curriculo_id, decisao, data_decisao) VALUES (%s, %s, %s)",
            (curriculo_id, decisao, datetime.now()),
        )
        cur.close()


def calcular_ajuste_aprendizado(empresa_id: int, competencias_candidato: list) -> float:
    """Sistema de aprendizado simples, calculado apenas com o histórico de
    decisões DA MESMA EMPRESA (não mistura aprendizado entre clientes).

    Correção #7: adicionado LIMIT 500 para evitar full scan em empresas
    com histórico muito grande. O ajuste continua representativo com uma
    janela recente de decisões.
    """
    if not competencias_candidato:
        return 0.0

    with conectar() as conn:
        cur = conn.cursor()
        # Só a decisão MAIS RECENTE de cada currículo conta. Antes, cada clique
        # virava uma linha e um candidato que o recrutador trocou de Aprovado
        # para Reprovado pesava duas vezes no aprendizado.
        cur.execute("""
            SELECT c.dados_json, d.decisao
            FROM (
                SELECT DISTINCT ON (curriculo_id) curriculo_id, decisao, data_decisao
                FROM decisoes
                ORDER BY curriculo_id, data_decisao DESC, id DESC
            ) d
            JOIN curriculos c ON c.id = d.curriculo_id
            WHERE c.empresa_id = %s
            ORDER BY d.data_decisao DESC
            LIMIT 500
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
            "SELECT * FROM curriculos WHERE empresa_id = %s AND vaga_id = %s "
            "ORDER BY score_final DESC NULLS LAST, id",
            (empresa_id, vaga_id),
        )
        resultados = []
        for linha in cur.fetchall():
            # Correção #2: tratamento explícito de JSON corrompido.
            try:
                dados = json.loads(linha["dados_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            dados["_id_banco"] = linha["id"]
            dados["hash_arquivo"] = linha["hash_arquivo"]
            # conteudo_bytes não é persistido (correção #12), então não
            # tenta recuperá-lo aqui.
            dados = _aplicar_decisao_manual(dados, linha.get("decisao_manual"))
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
        # Com a unicidade por (empresa, vaga, arquivo), soltar o currículo da
        # vaga (vaga_id = NULL) poderia colidir com um que já esteja sem vaga.
        # Nesse caso o duplicado é removido; os demais ficam sem vaga.
        cur.execute("""
            DELETE FROM curriculos c
            WHERE c.vaga_id = %s AND c.empresa_id = %s
              AND EXISTS (SELECT 1 FROM curriculos o
                          WHERE o.empresa_id = c.empresa_id
                            AND o.hash_arquivo = c.hash_arquivo
                            AND o.vaga_id IS NULL)
        """, (vaga_id, empresa_id))
        cur.execute("UPDATE curriculos SET vaga_id = NULL WHERE vaga_id = %s AND empresa_id = %s", (vaga_id, empresa_id))
        cur.execute("DELETE FROM vagas WHERE id = %s AND empresa_id = %s", (vaga_id, empresa_id))
        cur.close()


def limpar_banco():
    """Apaga TODOS os dados de TODAS as empresas.

    Correção #1: protegido contra execução acidental em produção.
    Só roda se AMBIENTE=teste estiver definido no ambiente.
    """
    ambiente = os.environ.get("AMBIENTE", "").lower()
    if ambiente != "teste":
        raise RuntimeError(
            "limpar_banco() só pode ser chamada com AMBIENTE=teste. "
            "Em produção, use o painel do banco de dados diretamente."
        )
    with conectar() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM decisoes")
        cur.execute("DELETE FROM curriculos")
        cur.execute("DELETE FROM vagas")
        cur.execute("DELETE FROM empresas")
        cur.close()
