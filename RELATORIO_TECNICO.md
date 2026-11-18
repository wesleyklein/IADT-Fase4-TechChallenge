# Relatório técnico do monitoramento multimodal
## IADT — Tech Challenge Fase 4

**Data de consolidação — 17/11/2026**  
**Versão — 1.0**

## 1. Objetivo e contexto

O projeto implementa um protótipo acadêmico de monitoramento multimodal para um cenário de fisioterapia e acompanhamento de paciente. A solução processa vídeo de movimento, áudio de relato, texto transcrito, séries temporais de sinais vitais e versões de uma prescrição. Os achados são associados ao paciente e reunidos em alertas com evidências e confirmação de recebimento.

A aplicação oferece captura de vídeo e áudio no navegador, envio de arquivos e reprodução de cenários sintéticos. O processamento local é combinado com serviços gerenciados Azure Speech e Azure AI Language. As regras e os limiares foram definidos para a demonstração acadêmica. A validação com gravações humanas anotadas permanece como etapa de avaliação do projeto.

## 2. Descrição do fluxo multimodal

### 2.1. Entrada e preparação dos dados

A interface em HTML, CSS e JavaScript utiliza as APIs getUserMedia e MediaRecorder para obter câmera e microfone com autorização no navegador. Vídeo e áudio são gravados individualmente e podem ser analisados na mesma sessão, associada a um identificador fictício de paciente.

O FastAPI recebe os arquivos e encaminha o processamento para os módulos Python. O FFmpeg, disponibilizado pelo pacote imageio-ffmpeg, converte o áudio para WAV PCM mono, com 16 bits e taxa de 16 kHz. O vídeo enviado é normalizado para um quadro de 640 × 480 pixels, preservando a proporção com preenchimento, e processado a 10 quadros por segundo. A prévia é disponibilizada em WebM.

Na interface, o limite é de 40 MB por arquivo, com duração entre 1 e 60 segundos. Os cenários sintéticos são gerados por fixtures.py e incluem vídeo, áudio, texto de referência, sinais vitais, prescrições e anotações dos desvios esperados.

### 2.2. Análise por modalidade

O vídeo humano é processado localmente pelo MediaPipe Pose Landmarker Lite. Os pontos de ombro, cotovelo e quadril sustentam o cálculo do ângulo de elevação do braço. A saída inclui amostras ao longo do tempo, cobertura de detecção, avaliação de janelas de quatro segundos e um quadro anotado com os pontos detectados.

Os vídeos esquemáticos dos cenários sintéticos são analisados por uma rotina OpenCV que mede a elevação do braço em pixels. Essa rotina fornece evidências temporais para testar o fluxo de monitoramento com dados controlados.

O áudio é enviado ao Azure Speech para transcrição. Em paralelo lógico ao tratamento textual, os dados acústicos do WAV são usados no cálculo de ritmo da fala, pausas e dispersão da intensidade.

A transcrição é enviada ao Azure AI Language para análise de sentimento e extração de frases-chave. Uma regra local identifica termos críticos e verifica marcadores de negação na oração. A evidência textual preserva o termo, sua posição na transcrição e a indicação de negação.

Os sinais vitais são comparados com faixas demonstrativas e verificados quanto à persistência em amostras consecutivas. As prescrições são comparadas com a versão anterior do mesmo item e unidade.

### 2.3. Fusão e geração dos alertas

Cada evento possui identificação, paciente, origem e horário com fuso. As evidências são combinadas por uma estratégia de fusão tardia baseada em regras. Primeiro são obtidos os achados de cada modalidade. Em seguida, achados do mesmo paciente são associados a um alerta dentro de uma janela máxima de dez segundos a partir da primeira evidência.

Na reprodução dos exemplos, os relatórios de áudio e vídeo já estão processados. O achado do áudio entra no fluxo ao término do WAV e o achado do movimento entra ao final da janela de vídeo correspondente. Sinais vitais e prescrições chegam conforme os horários dos eventos do cenário.

Para novas gravações enviadas pela interface, as evidências recebem o horário da requisição de análise no servidor. Os intervalos internos do exercício ficam preservados no resultado. A correlação entre gravações separadas depende dessa janela temporal.

O alerta recebe gravidade de atenção quando contém um tipo de achado. Com dois ou mais tipos distintos, sua gravidade passa a alta. A evidência guarda os valores, trechos, intervalos, regras e referências aos eventos que sustentaram a sinalização.

### 2.4. Apresentação e persistência

O painel apresenta as mídias, a transcrição, os indicadores de fala e os gráficos de acompanhamento dos cenários. A Central de alertas permite consultar as evidências e confirmar o recebimento, informando o responsável.

SQLite mantém eventos, achados, alertas, avisos e histórico de confirmações. Eventos idênticos podem ser reprocessados sem duplicação. Conflitos de conteúdo para um mesmo ID são sinalizados. Novas evidências reabrem um alerta previamente confirmado e preservam o histórico.

As credenciais Azure são lidas do arquivo local .env. O .gitignore exclui esse arquivo e os dados de execução.

## 3. Modelos e técnicas aplicados

| Tipo de dado | Modelo ou técnica | Implementação e saída |
|---|---|---|
| Vídeo humano | MediaPipe Pose Landmarker Lite, pré-treinado | Estima pontos corporais quadro a quadro. O cálculo geométrico local produz o ângulo entre braço e tronco na imagem. |
| Vídeo sintético | Segmentação por intensidade e região de interesse | OpenCV mede a altura dos pixels do braço em janelas de quatro segundos e compara o pico com o limiar de 70 px. |
| Áudio para transcrição | Reconhecimento de fala Azure Speech | Chamada autenticada à API REST para áudio curto. O idioma é informado por gravação, com pt-BR e pt-PT na interface. |
| Indicadores acústicos | Energia RMS, segmentação por amplitude e contagem temporal | NumPy calcula atividade de fala, pausas, palavras por minuto e dispersão da intensidade. |
| Texto | Serviços pré-treinados de sentimento e frases-chave do Azure AI Language | Chamadas SentimentAnalysis e KeyPhraseExtraction com idioma português e API 2024-11-01. |
| Termos críticos | Expressões regulares com tratamento de negação | Vocabulário inclui falta de ar, cansaço, cansado/cansada e dificuldade para/em falar. |
| Sinais vitais | Regras de faixa com persistência temporal | Mesma medida fora da faixa em duas amostras consecutivas válidas, separadas por até quatro segundos. |
| Prescrições | Comparação entre versões | Mesmo item e mesma unidade. Sinaliza razão entre valor atual e anterior maior ou igual a 2, ou menor ou igual a 0,5. |
| Fusão multimodal | Agrupamento por paciente, tempo e tipos de evidência | Janela máxima de dez segundos, atualização de gravidade e registro de justificativas. |

Os serviços Azure utilizam modelos gerenciados pelo provedor. A implementação utiliza as versões padrão disponibilizadas pelas APIs. A identificação exata dos modelos internos do provedor fica fora dos metadados registrados no relatório atual. O modelo MediaPipe é obtido como artefato pré-treinado. O projeto aplica inferência e regras demonstrativas sobre essas saídas.

### 3.1. Avaliação da pose humana

Para o lado selecionado, são utilizados os pontos de ombro, cotovelo e quadril. O cálculo corrige a diferença de proporção entre os eixos da imagem antes de calcular o ângulo por produto escalar.

Um quadro contribui para a medição quando os três pontos estão dentro da imagem e apresentam presença e visibilidade de pelo menos 0,6. Cada janela completa de quatro segundos precisa de cobertura mínima de 60%. Com cobertura suficiente, pico angular abaixo de 60° gera o achado de elevação reduzida. Janelas com cobertura menor recebem o estado de pose insuficiente.

### 3.2. Indicadores acústicos e texto

A energia é calculada em quadros de 20 ms. O limiar de atividade é definido como 8% do maior RMS, respeitando o mínimo absoluto de uma unidade de amplitude. Lacunas internas de pelo menos 200 ms são contabilizadas como pausas.

O ritmo usa a quantidade de palavras transcritas e o intervalo entre o primeiro e o último quadro ativo. A dispersão da intensidade corresponde à diferença entre os percentis 90 e 10 da intensidade dos quadros ativos, em dB relativos à escala digital.

A regra de negação verifica marcadores como “sem”, “não”, “nego” e “nega”, limitados à oração e à proximidade textual do termo. O sentimento é apresentado como resultado de linguagem. A criação do achado de áudio/texto depende da presença de termos críticos afirmados.

### 3.3. Parâmetros dos sinais vitais

| Medida | Faixa usada no cenário |
|---|---|
| Frequência cardíaca | 50 a 110 bpm |
| Pressão sistólica | 100 a 160 mmHg |
| Pressão diastólica | 65 a 100 mmHg |
| Saturação periférica de oxigênio | 94 a 100% |

Esses valores configuram os testes sintéticos e a regra demonstrativa. Medidas ausentes geram avisos e permanecem sem imputação. Entradas com valores ou unidades inválidos são registradas como inválidas e deixam de servir como referência para comparações posteriores.

## 4. Resultados obtidos

### 4.1. Condições da avaliação

A avaliação funcional utiliza dois cenários controlados, denominados normal e anômalo. Cada caso possui 11 eventos de entrada e um vídeo de 12 segundos, com 240 quadros. Os áudios são falas sintetizadas em pt-PT. A fala do caso anômalo foi sintetizada em velocidade menor para verificar a resposta dos indicadores acústicos.

As chamadas reais aos serviços Azure produziram os relatórios armazenados em data/generated/normal/analise_fase2.json e data/generated/anomalo/analise_fase2.json. Em 17/11/2026, esses resultados salvos foram conferidos, o fluxo de monitoramento foi reproduzido em banco temporário e os nove testes automatizados foram executados novamente com sucesso.

### 4.2. Comparação entre os cenários

| Resultado | Normal | Anômalo |
|---|---|---|
| Elevação máxima nos três ciclos sintéticos | 84, 84 e 84 px | 84, 60 e 84 px |
| Janelas de movimento com desvio | 0 | 1, entre 4 e 8 s |
| Duração do áudio | 4,210 s | 7,758 s |
| Ritmo da fala | 164,6 palavras/min | 95,5 palavras/min |
| Pausas de pelo menos 200 ms | 1 | 2 |
| Duração total das pausas internas | 0,9 s | 2,0 s |
| Dispersão da intensidade | 16,28 dB | 15,63 dB |
| Sentimento retornado | mixed | negative |
| Termos críticos afirmados | 0 | “cansado” e “falta de ar” |
| Alertas ao final da reprodução | 0 | 1, com gravidade alta |
| Tipos de evidência no alerta | 0 | 4 |

A transcrição do cenário normal foi “O exercício correu bem. Estou sem falta de ar.” A expressão “falta de ar” foi reconhecida como negada. Para o caso anômalo, o Azure retornou “Estou muito cansado. Sinto falta de ar durante o exercício.” Os dois termos foram reconhecidos como afirmados.

O Azure AI Language retornou as frases-chave “exercício”, “falta” e “ar” em ambos os casos. O tratamento de negação preservou a diferença de sentido entre as duas transcrições na geração dos achados.

### 4.3. Exemplos de anomalias detectadas

**Movimento reduzido.** No vídeo sintético anômalo, o segundo ciclo, entre 4 e 8 s, apresentou elevação de 60 px. O limiar configurado era 70 px. O relatório registrou o intervalo e a evidência de elevação reduzida. Nos outros dois ciclos, o pico foi de 84 px.

**Persistência de alterações nos sinais vitais.** Nas amostras de 4 e 6 s do caso anômalo, foram registrados 128 bpm, pressão de 95/62 mmHg e saturação de 89%. As quatro medidas ficaram fora das respectivas faixas demonstrativas nas duas amostras. A regra confirmou a persistência e abriu o alerta no segundo registro, em 6 s.

**Mudança de prescrição.** O item fictício ITEM-DEMO passou do valor 1 para 3 em 6 s, mantendo a unidade de demonstração. A razão igual a 3 satisfez a regra de alteração maior ou igual ao dobro. O achado preservou as referências à versão anterior e à atual.

**Relato de sintomas.** Na transcrição anômala, “cansado” e “falta de ar” apareceram como termos afirmados. O ritmo estimado foi de 95,5 palavras por minuto, acompanhado de duas pausas. O monitor usou os termos afirmados como gatilho textual e guardou o ritmo como evidência complementar.

**Alerta integrado.** Ao longo da reprodução, os achados de sinais vitais, prescrição, áudio/texto e movimento foram reunidos no mesmo alerta. Sua gravidade passou a alta ao incorporar o segundo tipo de evidência. Foi possível confirmar o recebimento e repetir a reprodução preservando a confirmação e a quantidade de eventos.

### 4.4. Pose humana e interface

O processamento de pose foi verificado tecnicamente com um clipe criado a partir de uma imagem humana de exemplo do MediaPipe. O teste apresentou cobertura de 100% dos quadros para os pontos selecionados e gerou uma imagem anotada. Esse teste verificou a execução do modelo e a produção das saídas visuais.

Os testes da interface incluíram upload de vídeo e áudio em WebM, conversão dos arquivos, transcrição real no Azure, identificação da negação no áudio sintético, reprodução das mídias, atualização dos gráficos e confirmação de um alerta com quatro evidências. Um arquivo inválido foi recusado, preservando o resultado anterior da sessão.

A avaliação da detecção de movimentos requer gravações humanas do exercício com intervalos anotados. A captura com webcam e microfone físicos permanece pendente nos registros de validação disponíveis.

### 4.5. Consistência e latência

Os nove testes de regressão verificam cenários normal e anômalo, fusão das evidências, repetição de eventos, conflito de conteúdo para o mesmo ID, concorrência, dados ausentes, unidades inválidas, rejeição de eventos atrasados, limite da janela temporal e confirmação/reabertura de alertas. A execução realizada em 17/11/2026 foi concluída com nove testes aprovados.

Na reprodução em velocidade original registrada durante o desenvolvimento, as atualizações de alerta levaram aproximadamente 4 a 11 ms entre o recebimento do evento e sua impressão no terminal. Esse intervalo mede a etapa local de monitoramento após a disponibilidade dos resultados multimodais. O tempo das chamadas Azure e da inferência de pose integra o tempo de análise das mídias e requer medição própria. Na interface, o campo de processamento registra o tempo local no servidor antes de enviar a resposta ao navegador.

Os dois cenários demonstram o atendimento das regras para os exemplos construídos. A estimativa de desempenho em uso mais amplo depende de uma base maior com anotações e medições de acertos, perdas e falsos alertas.

## 5. Organização da implementação

| Arquivo ou diretório | Responsabilidade |
|---|---|
| fixtures.py | Geração e validação dos cenários sintéticos |
| analyze.py | Análise do vídeo sintético, indicadores acústicos, integração Azure e termos críticos |
| media.py | Conversão das gravações e estimativa de pose em vídeo humano |
| monitor.py | Detecção por regras, fusão temporal, persistência e confirmação de alertas |
| server.py | API FastAPI e integração com a interface |
| setup_model.py | Obtenção do modelo MediaPipe Pose Landmarker Lite |
| web/ | Interface HTML, CSS e JavaScript |
| test_monitor.py | Testes de regressão do monitoramento |
| data/reference/ | WAVs sintéticos utilizados na geração dos casos |
| data/generated/ | Cenários gerados e relatórios por modalidade |
| data/sessions/ | Mídias, resultados e banco de cada sessão web |
| requirements.txt | Dependências Python fixadas |
| README.md | Instalação, configuração e comandos de demonstração |

## 6. Reprodução dos resultados

Na pasta do projeto, com Python, dependências e .env configurados, os comandos abaixo recriam os exemplos, consultam o Azure e executam o monitoramento. O banco pode ser mantido entre execuções para verificar o reprocessamento.

```powershell
.\.venv\Scripts\python.exe fixtures.py generate
.\.venv\Scripts\python.exe analyze.py todos
.\.venv\Scripts\python.exe monitor.py --db data/relatorio.sqlite3 replay todos --speed 1
.\.venv\Scripts\python.exe monitor.py --db data/relatorio.sqlite3 list
.\.venv\Scripts\python.exe -m unittest -v test_monitor
```

Para disponibilizar a interface e o modelo de pose, execute os comandos abaixo e abra http://localhost:8000 no navegador.

```powershell
.\.venv\Scripts\python.exe setup_model.py
.\.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8000
```

## 7. Etapas de validação pendentes

A próxima avaliação inclui gravações humanas anotadas para movimento e fala, uso dos dispositivos físicos de captura, verificação de instalação limpa e testes de indisponibilidade dos serviços Azure. Os resultados dessas etapas devem ser incorporados às próximas versões deste relatório e à spec de desenvolvimento.

## 8. Fontes e evidências

- Enunciado IADT - Fase 4 - Tech challenge.pdf, páginas 2 a 4, disponível na pasta de materiais da disciplina.
- Código-fonte dos módulos listados na seção 5.
- Relatórios locais data/generated/normal/analise_fase2.json e data/generated/anomalo/analise_fase2.json.
- Cenários e anotações locais data/generated/normal/cenario.json e data/generated/anomalo/cenario.json.
- Spec de desenvolvimento, versão 0.6, e registros de testes do projeto.

