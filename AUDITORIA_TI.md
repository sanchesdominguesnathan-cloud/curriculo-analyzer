# Auditoria de TI — curriculo-analyzer

Escopo: leitura completa de `parser.py`, `utils.py`, `keywords.py`, `score.py`, `ranking.py`, `database.py`, `forms_import.py`, `auth.py` e do fluxo de `app.py`. Cada falha abaixo foi **reproduzida por execução** (incluindo um PostgreSQL real de teste) antes de ser corrigida. Depois das correções: 57 testes automatizados passam; no código original, 51 desses 57 falham.

Gravidade: **CRÍTICA** = perde/duplica dado ou nota errada em escala · **ALTA** = resultado errado frequente ou brecha de segurança · **MÉDIA** = erro em casos específicos.

---

## 1. Falhas no NOME do candidato (corrigidas)

| # | Gravidade | Causa | Exemplo real |
|---|---|---|---|
| 1.1 | ALTA | O "caso geral" aceitava qualquer linha curta com iniciais maiúsculas | "Informações de Contato", "Dados Profissionais", "Carteira de Habilitação", "Redes Sociais" viravam o nome |
| 1.2 | ALTA | Filtro de caracteres apagava ligaduras de PDF (ﬁ, ﬂ) e acentos decompostos | "Filipe" → "lipe"; "José" → "Jose" |
| 1.3 | MÉDIA | `str.title()` | "Maria **Da** Silva", "Mcdonald", "D'Ávila" errados |
| 1.4 | MÉDIA | Última palavra de 2 letras igual a sigla de UF era descartada como endereço | "Ana Paula **Ma**" (MA), "Luiz Roberto **Pi**" (PI) sumiam |
| 1.5 | ALTA | Fallback devolvia "a primeira linha que sobrasse" | E-mail, telefone ou endereço virava o nome |
| 1.6 | MÉDIA | Em `.docx`, tabelas eram lidas só depois de todos os parágrafos | Modelos com nome dentro de tabela perdiam o nome |
| 1.7 | MÉDIA | Nome do arquivo só aceitava `-` como separador | `CV_Maria_Silva.docx` não era aproveitado |

**Correção:** validação por lista de palavras que nunca são nome, NFKC + recomposição de acentos, capitalização que respeita preposições e caixa mista, leitura de colunas lado a lado, fallback validado (senão "Não identificado").

## 2. "Resumo profissional" pulando/duplicando candidatos (corrigido)

Os sintomas vinham de **quatro causas diferentes**, em camadas:

| # | Gravidade | Onde | Causa | Efeito |
|---|---|---|---|---|
| 2.1 | **CRÍTICA** | `database.py` | Unicidade `(empresa, hash_arquivo)`: **uma linha por arquivo na empresa inteira**. O `ON CONFLICT` movia o candidato para a vaga nova | Analisar o mesmo currículo para a Vaga B **fazia o candidato sumir da Vaga A**, com a nota da B |
| 2.2 | **CRÍTICA** | `app.py` | Só `ValueError` era tratado no loop. PDF corrompido gera `PdfminerException`, `.docx` inválido gera `BadZipFile` | Um arquivo ruim **derrubava o lote**; os seguintes nunca eram analisados nem salvos |
| 2.3 | **CRÍTICA** | `app.py` | No reenvio, `dados = dict(ja_analisado)` reaproveitava nome, experiência e competências antigas | Resumo desatualizado e **nome errado preso para sempre**, mesmo após consertar o parser |
| 2.4 | ALTA | `forms_import.py` | Chave de deduplicação `timestamp\|e-mail` | Corrigir o e-mail na planilha **reimportava o candidato** (duplicado) |
| 2.5 | ALTA | `keywords.py` | `list(set(...))` | Ordem muda a cada reinício: "Principais competências" mudavam entre execuções |
| 2.6 | ALTA | `utils.py` | "Não identificado" tratado como dado válido | "possui aproximadamente Não identificado de experiência"; nome "Não identificado" virava sujeito da frase |
| 2.7 | MÉDIA | `database.py` | `ON CONFLICT` não atualizava `nome_candidato`, e-mail, telefone, cidade | Colunas do banco divergiam do JSON após reanálise |
| 2.8 | MÉDIA | `app.py` | Arquivo idêntico duas vezes no lote era sobrescrito em silêncio | Candidato "sumia" sem aviso |

**Correção:** unicidade por `(empresa, vaga, arquivo)` com migração automática e idempotente; loop com `try` por arquivo; reanálise **sempre do zero** (preserva só a decisão manual); chave do Forms baseada em timestamp + texto; lista de palavras determinística; avisos para arquivo repetido; nova coluna **"Duplicidade"** que sinaliza a mesma pessoa (e-mail, telefone ou nome iguais) enviada em arquivos diferentes.

## 3. Outras falhas encontradas (corrigidas)

| # | Gravidade | Falha |
|---|---|---|
| 3.1 | **CRÍTICA** | **Palavras-chave por substring**: "excel" casava com "excelente", "bi" com "habilidades", "erp" com "interpretação", "sap" com "sapato". As obrigatórias valem 60% da nota, então praticamente todo candidato "tinha" Excel e Power BI. Agora usa fronteira de palavra |
| 3.2 | ALTA | **Isolamento entre empresas**: `registrar_decisao(id)` não checava a empresa. Qualquer id (sequencial) alterava currículo de outra empresa e contaminava o aprendizado dela. Agora exige `empresa_id` e valida a decisão |
| 3.3 | ALTA | **Experiência dependia do dia da análise**: "Itaú 2018" caía num parser genérico que preenchia mês/dia com os de **hoje** |
| 3.4 | ALTA | **Seção de experiência cortada** por "habilidades/competências/formação" no meio de uma frase: currículo de 7 anos saía com 1 ano e 11 meses |
| 3.5 | ALTA | **Faculdade contada como experiência** quando não havia título de experiência reconhecido |
| 3.6 | MÉDIA | Contagem de meses não inclusiva ("jan a dez" = 35; "jun a jun" = 0) |
| 3.7 | ALTA | **"MBA" por substring**: "embalagem", "embarque", "combate" davam MBA (90 pontos de formação) |
| 3.8 | MÉDIA | Telefone: intervalo de anos ("2018-2020") era extraído como telefone |
| 3.9 | MÉDIA | Alerta falso "Fora da cidade": o parser gera "Niterói - RJ" e a vaga tem "Niterói" |
| 3.10 | MÉDIA | Filtros usavam regex: digitar `C++` ou `(` derrubava a tela; "Niteroi" não achava "Niterói" |
| 3.11 | MÉDIA | Aprendizado contava cada clique: candidato trocado de Aprovado para Reprovado pesava duas vezes |
| 3.12 | MÉDIA | `inicializar_banco()` (DDL) rodava a cada clique do Streamlit. Agora uma vez por processo |
| 3.13 | MÉDIA | Fuzzy match em palavras curtas ("sap", "bi") casava com qualquer palavra parecida |
| 3.14 | BAIXA | Faltava `.gitignore` (`__pycache__` e segredos locais podiam ser versionados) |

## 4. Pontos que NÃO alterei (decisão sua)

1. **Filtro "Cargo/competência"**: o cargo exercido pelo candidato não é extraído do currículo, então o filtro só acha o que já está no nome, resumo ou competências. Ou extrair cargos, ou renomear o filtro.
2. **Limite de tentativas de login** (`tentativas_login`) vive só no `session_state`: abrir outra aba zera. Para bloquear força bruta de verdade, registre tentativas por e-mail/IP no banco.
3. **LGPD**: o sistema guarda nome, e-mail, telefone e cidade sem política de retenção, consentimento ou rotina de exclusão. Vale definir prazo e um botão "excluir candidato".
4. **Arquivos `data` e `uploads` na raiz do repositório são arquivos vazios**, não pastas. Provavelmente acidente; remover.
5. **Excluir vaga**: a mensagem diz que os currículos "continuam no histórico geral", mas não existe tela de histórico; eles ficam sem vaga e invisíveis.
6. **Idiomas**: citar "Inglês" em qualquer lugar do texto já dá 40 pontos ("Não especificado"). Considerar exigir a seção de idiomas.
7. `SHEET_IDS_POR_EMPRESA` está fixo no código. Mover para o banco ou variável de ambiente.
8. Nome só com letras espaçadas ("B R U N O ...") sem nome de arquivo aproveitável sai como uma palavra só (limitação do PDF, sem solução segura).

## 5. Como aplicar

1. Faça **backup do banco** (a migração remove a restrição antiga e cria o índice novo no primeiro start; é idempotente, não apaga dados).
2. Aplique o patch (`git apply curriculo-analyzer-correcoes.patch`) ou copie os arquivos.
3. Rode `pytest tests/` (os de banco exigem `AMBIENTE=teste` e uma `DATABASE_URL` de **teste**, nunca a de produção).
4. **Reenvie os currículos** das vagas afetadas: a reanálise agora refaz nome, experiência e resumo (a decisão manual é mantida). Candidatos do Forms usam o nome digitado no formulário.
