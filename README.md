# OCR de Notas em PDF

Este projeto extrai dados de notas fiscais em PDF com estrategia hibrida:

- Leitura direta do texto do PDF (mais rapido, quando disponivel)
- OCR com EasyOCR apenas nas paginas sem texto embutido

Tecnologias usadas:

- PyMuPDF
- EasyOCR
- NumPy

Nao depende de Tesseract nem Poppler externo.

## Requisitos

- Python 3.13 (recomendado) com ambiente virtual ativo
- PDFs na pasta [PDF](PDF) ou caminho direto para um PDF

## Instalar dependencias

No PowerShell, dentro da pasta do projeto:

```powershell
.\venv\Scripts\python.exe -m pip install -r .\requirements.txt
```

## Como executar

### 1. Processar todos os PDFs da pasta (modo lote)

```powershell
.\venv\Scripts\python.exe .\pdf_ocr.py PDF
```

Se omitir o argumento, a pasta PDF e usada por padrao:

```powershell
.\venv\Scripts\python.exe .\pdf_ocr.py
```

No modo lote, o script processa todos os PDFs e gera um arquivo consolidado:

- [PDF/resultado.txt](PDF/resultado.txt)
- Separacao entre documentos por ----

### 2. Processar um unico PDF

```powershell
.\venv\Scripts\python.exe .\pdf_ocr.py ".\PDF\NF 17818 CIEB SEDE.pdf"
```

No modo arquivo unico, a saida padrao e um .txt com o mesmo nome do PDF.

## Formato de saida padrao (filtrada)

Por padrao, o script salva apenas os campos principais:

- Numero da Nota
- Unidade
- Endereco
- Razoes Sociais

Regra de negocio aplicada:

- Linhas com Qualycopy sao removidas da saida filtrada

## Gerar texto completo

Se quiser o texto completo extraido (sem filtro), use:

```powershell
.\venv\Scripts\python.exe .\pdf_ocr.py ".\PDF\NF 17818 CIEB SEDE.pdf" --full-text
```

## Opcoes disponiveis

- --lang por
	Idioma do OCR. Aceita combinacoes como por+eng.

- --dpi 170
	Resolucao base para OCR em paginas sem texto embutido.

- --max-pixels 2200000
	Limite de pixels por pagina no OCR (menor = mais rapido).

- --pages 1-3
	Processa apenas paginas especificas.

- --output CAMINHO
	Define arquivo de saida:
	- Modo lote: arquivo consolidado (padrao: resultado.txt)
	- Modo arquivo unico: .txt do documento

- --quiet
	Suprime mensagens de progresso.

- --full-text
	Salva texto completo em vez da saida filtrada.

## Exemplos rapidos

Executar silencioso no lote:

```powershell
.\venv\Scripts\python.exe .\pdf_ocr.py PDF --quiet
```

Definir arquivo consolidado customizado no lote:

```powershell
.\venv\Scripts\python.exe .\pdf_ocr.py PDF --output .\PDF\meu_resultado.txt
```

Processar apenas paginas 1 e 2 de um arquivo:

```powershell
.\venv\Scripts\python.exe .\pdf_ocr.py ".\PDF\NF 17818 CIEB SEDE.pdf" --pages 1-2
```

## Observaçoes

- Na primeira execucao, o EasyOCR pode baixar modelos automaticamente.
- Se houver erro de modulo nao encontrado, confirme o uso do Python do venv:

```powershell
.\venv\Scripts\python.exe .\pdf_ocr.py PDF
```
