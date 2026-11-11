"""Testes de regressão: nome, resumo, palavras-chave, experiência, ranking."""
import io
import subprocess
import sys

import pytest

import parser as P
import keywords as K
import utils as U
import ranking as R


# ----------------------------- NOME ---------------------------------------

@pytest.mark.parametrize("texto, esperado", [
    ("Maria da Silva Santos\nmaria@email.com\n(21) 99999-1111\n", "Maria da Silva Santos"),
    # bloco de contato / seção antes do nome (antes virava o nome do candidato)
    ("Informações de Contato\nMaria da Silva Santos\nmaria@email.com\n", "Maria da Silva Santos"),
    ("Dados Profissionais\nPedro Henrique Gomes\np@x.com\n", "Pedro Henrique Gomes"),
    ("Carteira de Habilitação\nPedro Henrique Gomes\np@x.com\n", "Pedro Henrique Gomes"),
    ("Redes Sociais\nPedro Henrique Gomes\np@x.com\n", "Pedro Henrique Gomes"),
    ("Curriculum Vitae\nPedro Henrique Gomes\np@x.com\n", "Pedro Henrique Gomes"),
    ("Educação\nJoão Pereira Lima\njoao@x.com\n", "João Pereira Lima"),
    # caixa-alta mantém preposição em minúsculas
    ("MARIA DA SILVA SANTOS\nmaria@email.com\n", "Maria da Silva Santos"),
    # ligadura "ﬁ" (U+FB01) de PDF: antes era apagada ("Filipe" -> "lipe")
    ("ﬁlipe Fialho Souza\nfilipe@x.com\n", "Filipe Fialho Souza"),
    ("ﬂávio Reis Souza\nf@x.com\n", "Flávio Reis Souza"),  # ligadura "ﬂ" expande, não some
    # acento decomposto e acento "solto"
    ("José Antonio Pereira\njose@x.com\n", "José Antonio Pereira"),
    ("Jos´e Antonio Pereira\njose@x.com\n", "José Antonio Pereira"),
    # caixa mista preservada
    ("Carlos McDonald Souza\nc@x.com\n", "Carlos McDonald Souza"),
    ("Fernanda D'Ávila Costa\nf@x.com\n", "Fernanda D'Ávila Costa"),
    # nome que termina em 2 letras iguais a sigla de UF (antes era descartado)
    ("Ana Paula Ma\nana@x.com\n", "Ana Paula Ma"),
    ("Luiz Roberto Pi\nl@x.com\n", "Luiz Roberto Pi"),
    # cargo antes do nome
    ("Gerente Comercial\nRoberto Alves Lima\nr@x.com\n", "Roberto Alves Lima"),
])
def test_extrair_nome(texto, esperado):
    assert P.extrair_nome(texto) == esperado


def test_nome_nao_vira_email_nem_telefone():
    assert P.extrair_nome("ana.souza@x.com\n(21) 99999-1111\n") == "Não identificado"


def test_nome_do_arquivo_com_varios_separadores():
    assert P.extrair_nome("B R U N O   C A R V A L H A E S\nb@x.com\n", "CV_Bruno_Carvalhaes.pdf") == "Bruno Carvalhaes"


def test_nome_em_duas_colunas():
    assert P.extrair_nome("Telefone (21) 99999-1111      Maria Souza Lima\n") == "Maria Souza Lima"


# ------------------------- PALAVRAS-CHAVE ----------------------------------

TEXTO_SEM_COMPETENCIAS = (
    "Pessoa com excelente comunicação, boas habilidades, interpretação de textos, "
    "companhia ambiental, copy de documentos, keyword research, sapato, sacramento."
)


@pytest.mark.parametrize("kw", ["excel", "power bi", "erp", "python", "sap", "sac", "rh"])
def test_sem_falso_positivo_por_substring(kw):
    achou, _ = K.encontrar_ocorrencias(TEXTO_SEM_COMPETENCIAS, kw)
    assert achou is False


def test_ainda_encontra_quando_existe():
    for texto, kw in [("Domino Excel avançado.", "excel"), ("Uso Power BI e SQL.", "power bi"),
                      ("Experiência com planilhas e SAP.", "sap")]:
        assert K.encontrar_ocorrencias(texto, kw)[0] is True


def test_lista_de_palavras_deterministica_e_sem_repeticao():
    obr = {"excel": 8, "sql": 9, "python": 10}
    des = {"ingles": 6, "SQL": 3}
    esperado = ["python", "sql", "excel", "ingles"]
    assert K.montar_lista_palavras(obr, des) == esperado

    # mesma resposta em processos com hash seeds diferentes
    cod = ("import sys; sys.path.insert(0,'.'); import keywords as K;"
           "print(K.montar_lista_palavras({'excel':8,'sql':9,'python':10},{'ingles':6,'SQL':3}))")
    saidas = {subprocess.run([sys.executable, "-c", cod], capture_output=True, text=True,
                             env={"PYTHONHASHSEED": s, "PATH": ""}).stdout for s in ("1", "2", "3")}
    assert len(saidas) == 1


# ------------------------------ RESUMO -------------------------------------

def test_resumo_sem_experiencia_nao_usa_texto_nao_identificado_como_valor():
    r = U.gerar_resumo_candidato({"nome": "Ana Souza", "experiencia_formatada": "Não identificado",
                                  "competencias_encontradas": []})
    assert "aproximadamente Não identificado" not in r
    assert "não identificada" in r


def test_resumo_nome_nao_identificado_vira_sujeito_generico():
    r = U.gerar_resumo_candidato({"nome": "Não identificado", "experiencia_formatada": "2 anos"})
    assert r.startswith("Candidato(a)")
    assert "Não identificado possui" not in r


def test_resumo_idioma_sem_nivel():
    r = U.gerar_resumo_candidato({"nome": "Ana", "experiencia_formatada": "1 ano",
                                  "idiomas": {"Inglês": "Não especificado", "Espanhol": None}})
    assert "(Não especificado)" not in r and "(None)" not in r
    assert "Inglês, Espanhol" in r


def test_resumo_usa_nome_de_exibicao_da_formacao():
    d = {"nome": "Ana", "experiencia_formatada": "1 ano", "formacao_nivel": "Graduação"}
    nivel, area = P.extrair_formacao("Graduação em Administração e Gestão Comercial")
    d["formacao_area"] = area
    assert "Administração" in U.gerar_resumo_candidato(d)


# ---------------------------- EXPERIÊNCIA ----------------------------------

def test_palavra_antes_do_ano_nao_usa_data_de_hoje():
    per = U.extrair_periodos("Experiência\nBanco Itaú 2018 - 2020\n")
    assert per[0][0].year == 2018 and per[0][0].month == 1 and per[0][0].day == 1
    assert U.calcular_meses_experiencia(per) == 36


def test_periodos_adjacentes_somam_sem_perder_mes():
    per = U.extrair_periodos("01/2020 - 06/2020  e  07/2020 - 12/2020")
    assert U.calcular_meses_experiencia(per) == 12


def test_secao_nao_cortada_por_palavra_no_corpo():
    cv = ("Experiência Profissional\nAnalista - Empresa A (01/2015 - 12/2016)\n"
          "Desenvolvi habilidades de comunicação e liderança.\n"
          "Analista Sênior - Empresa B (01/2017 - 12/2021)\n"
          "Formação Acadêmica\nAdministração 2010 - 2014\n")
    meses, _ = P.calcular_experiencia_total(cv)
    assert meses == 84


def test_faculdade_nao_conta_como_experiencia():
    cv = "Trajetória\nAnalista - Empresa A (01/2019 - 12/2020)\nFormação\nAdministração 2012 - 2016\n"
    assert P.calcular_experiencia_total(cv)[0] == 24


def test_telefone_ignora_intervalo_de_anos():
    assert P.extrair_telefone("Experiência\nEmpresa X 2018-2020\nContato: (21) 99999-1111\n") == "(21) 99999-1111"


def test_mba_nao_casa_dentro_de_outras_palavras():
    assert P.extrair_formacao("Trabalhei com embalagem e embarque de mercadorias.") == (None, None)
    assert P.extrair_formacao("Possui MBA em Gestão Financeira")[0] == "MBA"


# ------------------------- ARQUIVOS CORROMPIDOS ----------------------------

@pytest.mark.parametrize("nome, dados", [("x.pdf", b"isto nao e um pdf"), ("x.docx", b"nao e zip"), ("x.docx", b"")])
def test_arquivo_corrompido_vira_valueerror(nome, dados):
    with pytest.raises(ValueError):
        P.processar_curriculo(nome, dados, ["excel"])


def test_docx_le_tabela_na_ordem_do_documento():
    import docx
    d = docx.Document()
    d.add_paragraph("Texto antes da tabela")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text = "Maria da Silva Santos"
    t.rows[0].cells[1].text = "maria@x.com"
    d.add_paragraph("Texto depois da tabela")
    buf = io.BytesIO(); d.save(buf)
    texto = P.extrair_texto_docx(buf.getvalue())
    assert texto.index("Texto antes") < texto.index("Maria da Silva") < texto.index("Texto depois")


# -------------------------------- RANKING ----------------------------------

def test_cidade_com_uf_nao_gera_alerta_falso():
    cands = [{"nome": "Ana", "cidade": "Niterói - RJ", "score_final": 80, "hash_arquivo": "h1"},
             {"nome": "Bia", "cidade": "São Gonçalo", "score_final": 70, "hash_arquivo": "h2"}]
    df = R.montar_tabela_resultados(cands, cidade_vaga="Niterói")
    assert df.loc[df.Nome == "Ana", "Alerta"].iloc[0] == ""
    assert "Fora da cidade" in df.loc[df.Nome == "Bia", "Alerta"].iloc[0]


def test_filtros_nao_quebram_com_caracteres_especiais_e_ignoram_acento():
    cands = [{"nome": "Ana", "cidade": "Niterói - RJ", "score_final": 80, "hash_arquivo": "h1",
              "competencias_encontradas": ["C++"]}]
    df = R.montar_tabela_resultados(cands)
    assert len(R.aplicar_filtros(df, cidade="niteroi")) == 1
    assert len(R.aplicar_filtros(df, cargo="C++")) == 1
    assert len(R.aplicar_filtros(df, cargo="(")) == 0


def test_candidato_duplicado_e_sinalizado():
    cands = [
        {"nome": "Ana Lima", "email": "ana@x.com", "score_final": 80, "hash_arquivo": "h1"},
        {"nome": "Ana L. Lima", "email": "ANA@x.com", "score_final": 70, "hash_arquivo": "h2"},
        {"nome": "Bia Souza", "email": "bia@x.com", "score_final": 60, "hash_arquivo": "h3"},
    ]
    df = R.montar_tabela_resultados(cands)
    dup = dict(zip(df["Nome"], df["Duplicidade"] != ""))
    assert dup == {"Ana Lima": True, "Ana L. Lima": True, "Bia Souza": False}


def test_candidato_sem_dados_de_contato_nao_e_duplicado():
    cands = [{"nome": "Não identificado", "score_final": 1, "hash_arquivo": "a"},
             {"nome": "Não identificado", "score_final": 2, "hash_arquivo": "b"}]
    assert (R.montar_tabela_resultados(cands)["Duplicidade"] == "").all()


# -------------------------------- CARGOS -----------------------------------

CV_CARGOS = """Maria Silva
Experiência Profissional
Analista Financeiro Sênior - Banco Itaú (01/2019 - 12/2022)
Auxiliei o analista de contas na rotina de fechamento.
Responsável pelo atendimento ao cliente e conciliação bancária.
ASSISTENTE ADMINISTRATIVO | Empresa XYZ | 2016 - 2018
Caixa Econômica Federal, Atendente, 2014-2016
Jovem Aprendiz - Loja ABC (2012 - 2014)
Formação
Técnico em Administração 2010 - 2012
"""


def test_extrai_cargos_e_ignora_frases_e_empresas():
    assert P.extrair_cargos(CV_CARGOS) == [
        "Analista Financeiro Sênior", "Assistente Administrativo", "Atendente", "Jovem Aprendiz",
    ]


def test_sem_cargos_devolve_lista_vazia():
    assert P.extrair_cargos("Texto livre sem nenhuma experiência listada.") == []


def test_filtro_de_cargo_acha_pelo_cargo_exercido():
    cands = [
        {"nome": "Maria", "score_final": 80, "hash_arquivo": "a", "cargos": ["Analista Financeiro Sênior"]},
        {"nome": "João", "score_final": 70, "hash_arquivo": "b", "cargos": ["Vendedor"]},
    ]
    df = R.montar_tabela_resultados(cands)
    assert list(R.aplicar_filtros(df, cargo="analista financeiro")["Nome"]) == ["Maria"]


def test_resumo_inclui_cargos():
    r = U.gerar_resumo_candidato({"nome": "Ana", "experiencia_formatada": "2 anos", "cargos": ["Analista", "Assistente"]})
    assert "Cargos: Analista, Assistente." in r


# -------------------------------- IDIOMAS ----------------------------------

@pytest.mark.parametrize("texto, esperado", [
    ("Experiência\nAtendimento a clientes ingleses e inglês técnico no dia a dia.\n", {}),
    ("Currículo em português. Analista.", {}),
    ("Perfil\nTenho inglês avançado e espanhol básico.\n", {"Inglês": "Avançado", "Espanhol": "Básico"}),
    ("Fluente em inglês, vivência no exterior.", {"Inglês": "Fluente"}),
    ("Idiomas\nInglês - Avançado\nEspanhol - Básico\nFormação\nGraduação em Direito\n",
     {"Inglês": "Avançado", "Espanhol": "Básico"}),
    ("Idiomas\nInglês\nFrancês\n", {"Inglês": "Não especificado", "Francês": "Não especificado"}),
    ("Idiomas: Inglês fluente, Espanhol intermediário", {"Inglês": "Fluente", "Espanhol": "Intermediário"}),
    ("Idiomas\nInglês\nAvançado\nEspanhol\nBásico\n", {"Inglês": "Avançado", "Espanhol": "Básico"}),
    ("Idiomas\nInglês, Espanhol básico\n", {"Inglês": "Não especificado", "Espanhol": "Básico"}),
    # citar o idioma DEPOIS de encerrada a seção não acrescenta idioma
    ("Idiomas\nInglês avançado\nExperiência\nTrabalhei com inglês e francês.\n", {"Inglês": "Avançado"}),
])
def test_extrair_idiomas(texto, esperado):
    assert P.extrair_idiomas(texto) == esperado
