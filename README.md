# 📄 Sistema de Análise Automática de Currículos

Aplicativo em Python/Streamlit para triagem automática de candidatos:
lê currículos em PDF e DOCX, compara com uma vaga e palavras-chave
cadastradas pela recrutadora, calcula uma pontuação e classifica cada
candidato como **Aprovado**, **Talvez** ou **Reprovado**.

## Estrutura do projeto

```
curriculo_analyzer/
├── app.py          # Interface Streamlit (tela principal)
├── parser.py       # Extração de texto e campos dos currículos (PDF/DOCX)
├── keywords.py     # Base de palavras-chave, pesos e sinônimos
├── score.py        # Cálculo da pontuação final
├── ranking.py      # Ordenação, montagem da tabela e filtros
├── database.py     # Persistência em SQLite + sistema de aprendizado
├── utils.py        # Funções auxiliares (datas, hash, resumo automático)
├── requirements.txt
└── data/           # Banco de dados SQLite (criado automaticamente)
```

## Como executar localmente

### 1. Pré-requisitos
- Python 3.10 ou superior instalado.

### 2. Criar ambiente virtual (recomendado)

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

### 3. Instalar dependências

```bash
pip install -r requirements.txt
```

### 4. Executar a aplicação

```bash
streamlit run app.py
```

O navegador abrirá automaticamente em `http://localhost:8501`.

## Como usar

1. **Upload de currículos**: envie um ou vários arquivos PDF/DOCX.
2. **Descrição da vaga**: cole o texto da vaga (usado como contexto).
3. **Palavras-chave**: digite as palavras obrigatórias e desejáveis
   separadas por vírgula (ex: `Excel, Power BI, SAP`). Sinônimos como
   "planilhas", "BI", "recursos humanos" já são reconhecidos automaticamente.
4. Ajuste os **pesos** de cada palavra-chave se desejar (expansor "Ajustar pesos").
5. Clique em **Analisar**. O sistema extrai os dados, pontua e monta o ranking.
6. Use os **filtros** (cidade, experiência, idioma, formação, pontuação mínima, cargo)
   para refinar a lista.
7. Clique em **Aprovar/Reprovar** em cada candidato — essas decisões alimentam
   o sistema de aprendizado, que passa a ajustar a pontuação de futuros
   candidatos com competências parecidas.
8. Exporte os resultados em **Excel, CSV ou PDF**.
9. Veja o **Dashboard** com gráficos de distribuição de notas, competências
   mais encontradas, cidades e formação.

Currículos já analisados anteriormente (mesmo arquivo) não são reprocessados —
o sistema identifica duplicidade pelo hash do conteúdo do arquivo.

## Ajustando a base de palavras-chave e sinônimos

A base de sinônimos fica em `keywords.py`, no dicionário `BASE_SINONIMOS`.
Para adicionar uma nova palavra-chave com sinônimos, inclua uma entrada:

```python
"nova_palavra_chave": ["variação 1", "variação 2", "expressão de contexto"],
```

## Publicando a aplicação (deploy)

### Opção 1 — Streamlit Community Cloud (gratuito, mais simples)
1. Suba o projeto para um repositório no GitHub.
2. Acesse [share.streamlit.io](https://share.streamlit.io) e conecte sua conta GitHub.
3. Selecione o repositório, o branch e o arquivo `app.py`.
4. Clique em "Deploy". O Streamlit Cloud instala as dependências do
   `requirements.txt` automaticamente.

### Opção 2 — Servidor próprio (VPS) com Docker
Crie um `Dockerfile` na raiz do projeto:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

Depois:

```bash
docker build -t curriculo-analyzer .
docker run -p 8501:8501 -v $(pwd)/data:/app/data curriculo-analyzer
```

### Opção 3 — Servidores como Render, Railway ou Heroku
1. Conecte o repositório do GitHub à plataforma escolhida.
2. Defina o comando de start: `streamlit run app.py --server.port=$PORT --server.address=0.0.0.0`.
3. Garanta que o volume/pasta `data/` seja persistente (algumas plataformas
   resetam o sistema de arquivos a cada deploy — nesse caso, considere migrar
   o `database.py` para usar um banco externo como PostgreSQL).

## Limitações conhecidas e próximos passos sugeridos

- PDFs escaneados como imagem (sem camada de texto) não são lidos — seria
  necessário adicionar OCR (ex: `pytesseract`) para esses casos.
- A extração de nome, cidade e datas usa regras heurísticas e pode falhar em
  currículos com formatação muito incomum; revisar manualmente é recomendado.
- O "sistema de aprendizado" atual é uma heurística simples baseada em
  competências em comum. Para um modelo mais robusto, pode-se treinar um
  classificador (ex: `scikit-learn`) usando o histórico de decisões salvo em
  `data/curriculos.db` como conjunto de treinamento.
