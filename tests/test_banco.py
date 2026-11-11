"""Testes de banco: rodam só com DATABASE_URL + AMBIENTE=teste (nunca em produção).

    AMBIENTE=teste DATABASE_URL=postgresql://user@host:5432/banco_de_teste pytest tests/
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL") or os.environ.get("AMBIENTE", "").lower() != "teste",
    reason="defina DATABASE_URL e AMBIENTE=teste para rodar os testes de banco",
)

import database as db  # noqa: E402


@pytest.fixture()
def cenario():
    db.inicializar_banco()
    db.limpar_banco()
    e1 = db.criar_empresa("Empresa 1", "e1@x.com", "Senha123!")
    e2 = db.criar_empresa("Empresa 2", "e2@x.com", "Senha123!")
    vA = db.salvar_vaga(e1, "Vaga A", "A", {"excel": 8}, {}, "Niterói")
    vB = db.salvar_vaga(e1, "Vaga B", "B", {"sql": 9}, {}, "Niterói")
    yield {"e1": e1, "e2": e2, "vA": vA, "vB": vB}
    db.limpar_banco()


def _dados(nome="Maria Silva", score=80, **extra):
    return {"nome": nome, "email": "maria@x.com", "score_final": score, "status": "Aprovado", **extra}


def test_mesmo_curriculo_em_duas_vagas_nao_some_da_primeira(cenario):
    e1, vA, vB = cenario["e1"], cenario["vA"], cenario["vB"]
    db.salvar_curriculo(e1, "h", "m.pdf", _dados(score=85), "A", vaga_id=vA)
    db.salvar_curriculo(e1, "h", "m.pdf", _dados(score=30), "B", vaga_id=vB)
    assert [c["score_final"] for c in db.listar_curriculos_por_vaga(e1, vA)] == [85]
    assert [c["score_final"] for c in db.listar_curriculos_por_vaga(e1, vB)] == [30]


def test_reanalise_na_mesma_vaga_atualiza_sem_duplicar(cenario):
    e1, vA = cenario["e1"], cenario["vA"]
    id1 = db.salvar_curriculo(e1, "h", "m.pdf", _dados(score=50), "A", vaga_id=vA)
    id2 = db.salvar_curriculo(e1, "h", "m.pdf", _dados(score=90), "A", vaga_id=vA)
    assert id1 == id2
    lista = db.listar_curriculos_por_vaga(e1, vA)
    assert len(lista) == 1 and lista[0]["score_final"] == 90


def test_curriculo_sem_vaga_tambem_nao_duplica(cenario):
    e1 = cenario["e1"]
    a = db.salvar_curriculo(e1, "h", "m.pdf", _dados(), "", vaga_id=None)
    b = db.salvar_curriculo(e1, "h", "m.pdf", _dados(), "", vaga_id=None)
    assert a == b


def test_colunas_desnormalizadas_acompanham_a_reanalise(cenario):
    e1, vA = cenario["e1"], cenario["vA"]
    db.salvar_curriculo(e1, "h", "j.pdf", {"nome": "Informações De Contato", "email": None, "score_final": 1, "status": "Reprovado"}, "", vaga_id=vA)
    db.salvar_curriculo(e1, "h", "j.pdf", {"nome": "João Pereira", "email": "j@x.com", "score_final": 5, "status": "Reprovado"}, "", vaga_id=vA)
    with db.conectar() as c:
        cur = c.cursor()
        cur.execute("select nome_candidato, email from curriculos where hash_arquivo='h'")
        linha = cur.fetchone()
    assert linha["nome_candidato"] == "João Pereira" and linha["email"] == "j@x.com"


def test_ja_analisado_e_por_vaga(cenario):
    e1, vA, vB = cenario["e1"], cenario["vA"], cenario["vB"]
    db.salvar_curriculo(e1, "h", "m.pdf", _dados(), "A", vaga_id=vA)
    assert db.curriculo_ja_analisado(e1, "h", vA) is not None
    assert db.curriculo_ja_analisado(e1, "h", vB) is None


def test_decisao_de_outra_empresa_e_bloqueada(cenario):
    e1, e2, vA = cenario["e1"], cenario["e2"], cenario["vA"]
    id_maria = db.salvar_curriculo(e1, "h", "m.pdf", _dados(), "A", vaga_id=vA)
    with pytest.raises(PermissionError):
        db.registrar_decisao(e2, id_maria, "Reprovado")
    assert db.listar_curriculos_por_vaga(e1, vA)[0]["status"] == "Aprovado"
    db.registrar_decisao(e1, id_maria, "Reprovado")  # a dona consegue
    assert db.listar_curriculos_por_vaga(e1, vA)[0]["status"] == "Reprovado"


def test_decisao_invalida_e_recusada(cenario):
    e1, vA = cenario["e1"], cenario["vA"]
    i = db.salvar_curriculo(e1, "h", "m.pdf", _dados(), "A", vaga_id=vA)
    with pytest.raises(ValueError):
        db.registrar_decisao(e1, i, "qualquer coisa")


def test_excluir_vaga_nao_quebra_com_mesmo_arquivo_em_duas_vagas(cenario):
    e1, vA, vB = cenario["e1"], cenario["vA"], cenario["vB"]
    db.salvar_curriculo(e1, "h", "m.pdf", _dados(), "A", vaga_id=vA)
    db.salvar_curriculo(e1, "h", "m.pdf", _dados(), "B", vaga_id=vB)
    db.excluir_vaga(e1, vA)
    db.excluir_vaga(e1, vB)  # antes da correção do índice, o 2º viraria colisão


def test_aprendizado_conta_so_a_ultima_decisao(cenario):
    e1, vA = cenario["e1"], cenario["vA"]
    i = db.salvar_curriculo(e1, "h", "m.pdf", _dados(competencias_encontradas=["excel"]), "A", vaga_id=vA)
    db.registrar_decisao(e1, i, "Aprovado")
    db.registrar_decisao(e1, i, "Reprovado")
    # 1 currículo, decisão final Reprovado: (0-1)/1 * 10 = -10 (não -0 nem 0)
    assert db.calcular_ajuste_aprendizado(e1, ["excel"]) == -10.0


def test_forms_nao_duplica_quando_email_e_corrigido(cenario, monkeypatch):
    import forms_import as F
    e1 = cenario["e1"]
    linha = {"Carimbo de data/hora": "10/11/2026 10:00:00", "Nome completo do candidato": "Ana Lima",
             "E-mail do candidato": "ana@x.com", "Telefone": "", "Vaga": "Vaga A", "Cidade do candidato": "Niterói",
             "Cole aqui o texto do currículo": "Experiência Profissional\nAnalista Empresa X (01/2018 - 12/2020)\nExcel e SQL."}
    linhas = [linha]
    monkeypatch.setattr(F, "_ler_linhas_planilha", lambda sid: linhas)
    assert F.importar_candidaturas_forms(e1, "x")["importados"] == 1
    assert F.importar_candidaturas_forms(e1, "x")["importados"] == 0
    linha["E-mail do candidato"] = "ana.lima@x.com"   # recrutadora corrigiu o e-mail
    assert F.importar_candidaturas_forms(e1, "x")["importados"] == 0
    assert len(db.listar_curriculos_por_vaga(e1, cenario["vA"])) == 1
