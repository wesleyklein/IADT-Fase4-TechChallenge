# Relatório de entrega — Tech Challenge Fase 4
## Monitoramento multimodal de pacientes

**Grupo 130**

| Integrante | Usuário / RM | E-mail |
|---|---|---|
| Wesley Adolpho de Lima Klein | rm371167 | wesleymga@gmail.com |

**Código-fonte** — [Repositório no GitHub](https://github.com/wesleyklein/IADT-Fase4-TechChallenge)  
**Vídeo de apresentação** — [Demonstração no YouTube](https://youtu.be/IDkg92BTiGg)

## 1. Descrição do fluxo multimodal

A solução é um protótipo acadêmico de acompanhamento de pacientes em um cenário de fisioterapia. Ela combina vídeo, áudio, texto, sinais vitais e prescrições para identificar desvios e apresentar alertas com evidências à equipe simulada.

O fluxo começa na interface web, desenvolvida em HTML, CSS e JavaScript. O usuário pode gravar vídeo pela webcam, gravar áudio pelo microfone ou enviar arquivos. O servidor Python, executado com FastAPI e Uvicorn, recebe as mídias e prepara os formatos com FFmpeg.

O vídeo é analisado localmente para estimar a pose e a elevação do braço. O áudio é transcrito pelo Azure Speech to Text. A transcrição segue para o Azure AI Language, correspondente aos recursos de Text Analytics, para análise de sentimento e extração de frases-chave. Regras locais identificam termos críticos e negação, enquanto indicadores acústicos descrevem ritmo, pausas e intensidade da fala.

Os achados são correlacionados por paciente em uma janela de até dez segundos. Nos cenários de exemplo, são combinados com séries de sinais vitais e alterações de prescrições. O painel apresenta gráficos, resultados por modalidade e alertas que podem ter o recebimento confirmado. SQLite armazena os eventos, as evidências e o histórico de confirmações.

A reprodução dos cenários utiliza análises de mídia previamente calculadas e disponibiliza os achados ao longo do fluxo temporal. Novas gravações são processadas após o envio pelo usuário.

## 2. Modelos e técnicas aplicados em cada tipo de dado

| Tipo de dado | Modelo ou técnica | Aplicação |
|---|---|---|
| Vídeo de pessoa | MediaPipe Pose Landmarker Lite, OpenCV e NumPy | Identificação de ombro, cotovelo e quadril e cálculo do ângulo entre braço e tronco. Janelas de quatro segundos são avaliadas com referência demonstrativa de 60° e cobertura mínima de pose de 60%. |
| Vídeo sintético | OpenCV com região de interesse e limiar de intensidade | Medição da elevação do braço em pixels para verificar o cenário controlado. O limiar é de 70 px por ciclo. |
| Áudio | Azure Speech to Text e processamento acústico com NumPy | Transcrição e cálculo de palavras por minuto, pausas de pelo menos 200 ms e dispersão da intensidade. |
| Texto | Azure AI Language e expressões regulares | Sentimento, frases-chave e identificação de termos como “cansado” e “falta de ar”, incluindo tratamento de negação. |
| Sinais vitais | Regras de faixa com persistência temporal | Detecção de medidas fora das faixas demonstrativas em duas amostras consecutivas, separadas por até quatro segundos. |
| Prescrições | Comparação de versões do mesmo item e unidade | Sinalização de valor duplicado ou reduzido à metade em relação à versão anterior. |
| Fusão multimodal | Agrupamento por paciente, tempo e tipo de evidência | Atualização do alerta e atribuição de prioridade alta quando reúne dois ou mais tipos de achado. |

As faixas demonstrativas dos sinais vitais são de 50 a 110 bpm para frequência cardíaca, 100 a 160 mmHg para pressão sistólica, 65 a 100 mmHg para pressão diastólica e 94 a 100% para saturação. Esses parâmetros foram definidos para o protótipo acadêmico.

## 3. Resultados obtidos e exemplos de anomalias detectadas

A avaliação funcional utilizou dois cenários sintéticos, cada um com 11 eventos de entrada e vídeo de 12 segundos. Os áudios sintetizados em português europeu foram transcritos em chamadas reais ao Azure.

| Resultado | Cenário normal | Cenário com desvios |
|---|---|---|
| Desvios no vídeo sintético | Nenhum | Elevação reduzida entre 4 e 8 s |
| Ritmo da fala | 164,6 palavras/min | 95,5 palavras/min |
| Pausas de pelo menos 200 ms | 1 | 2 |
| Sentimento retornado | Misto | Negativo |
| Termos críticos | “Falta de ar” identificado como negado | “Cansado” e “falta de ar” identificados como afirmados |
| Alertas gerados | 0 | 1, com quatro tipos de evidência e prioridade alta |

No cenário com desvios, o segundo ciclo do vídeo apresentou elevação de 60 px, abaixo do limiar de 70 px. Nos sinais vitais, duas amostras registraram frequência cardíaca de 128 bpm, pressão de 95/62 mmHg e saturação de 89%, satisfazendo a regra de persistência. A prescrição fictícia passou do valor 1 para 3, e a transcrição incluiu o relato “Estou muito cansado. Sinto falta de ar durante o exercício.”

Esses achados foram reunidos em um alerta com evidências de movimento, sinais vitais, prescrição e áudio/texto. A confirmação do recebimento foi registrada com responsável e horário. O reprocessamento preservou os registros e evitou duplicação.

Os nove testes automatizados de regressão passaram, cobrindo também dados ausentes, unidades inválidas, concorrência e reabertura após novas evidências. A interface foi verificada com uploads WebM, integração Azure, reprodução de mídia e confirmação de alertas. A execução do modelo de pose humana foi verificada em um clipe criado a partir de imagem de exemplo, com geração de quadro anotado.

Os resultados apresentados correspondem aos exemplos controlados. A avaliação de desempenho em gravações humanas anotadas permanece como continuidade do projeto. O código-fonte, as instruções de execução e o vídeo de apresentação estão nos links indicados no início deste relatório.

