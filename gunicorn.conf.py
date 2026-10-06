"""
Configuração do gunicorn (servidor WSGI de produção), lida automaticamente quando o
gunicorn é iniciado a partir da raiz do projeto. Todos os valores são ajustáveis por
variável de ambiente.

Timeout: a geração do ZIP de folhas-resposta (~0,4 s/aluno), a do ZIP de cadernos nominais
(~1 a 3 s/aluno, conforme o tamanho da prova) e a correção óptica em lote (~0,25 s/folha)
rodam dentro da requisição; o padrão de 30 s do gunicorn estouraria em
turmas grandes, por isso o padrão aqui é 120 s (o proxy HTTP na frente precisa de um
timeout igual ou maior).
"""

import os

bind = os.environ.get('GUNICORN_BIND', '0.0.0.0:8000')
workers = int(os.environ.get('GUNICORN_WORKERS', '3'))
timeout = int(os.environ.get('GUNICORN_TIMEOUT', '120'))
graceful_timeout = int(os.environ.get('GUNICORN_GRACEFUL_TIMEOUT', '30'))
# Recicla workers periodicamente (mitiga crescimento de memória do WeasyPrint/OpenCV).
max_requests = int(os.environ.get('GUNICORN_MAX_REQUESTS', '500'))
max_requests_jitter = int(os.environ.get('GUNICORN_MAX_REQUESTS_JITTER', '50'))

accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')
