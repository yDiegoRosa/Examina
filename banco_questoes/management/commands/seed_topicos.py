import logging

from django.core.management.base import BaseCommand
from django.db import transaction

from banco_questoes.models import Disciplina, Questao, Topico

logger = logging.getLogger(__name__)

TOPICOS_DATA = {
    'Construção de Aplicações WEB (CAW)': [
        (
            'HTML5 Semântico e Acessibilidade Web',
            'Estruturação de documentos modernos e boas práticas de acessibilidade.',
        ),
        ('CSS3 Avançado, Flexbox e Grid Layout', 'Estilização moderna, design responsivo, CSS Grid e Flexbox.'),
        (
            'JavaScript ES6+, DOM e Fetch API',
            'Manipulação do DOM, Promises, async/await e requisições HTTP assíncronas.',
        ),
        (
            'Django Framework: MTV, Views e Templates',
            'Arquitetura Model-Template-View, rotas, views baseadas em função e classes.',
        ),
        (
            'Django ORM, Formulários e Validações',
            'Consultas relacionais, migrations, ModelForms e validação de regras de negócio.',
        ),
        (
            'Arquitetura RESTful e APIs JSON',
            'Criação e consumo de endpoints REST, serialização de dados e autenticação.',
        ),
        ('Segurança em Aplicações Web', 'Prevenção contra CSRF, XSS, SQL Injection e gerenciamento de sessões.'),
    ],
    'Desenvolvimento de Tecnologias WEB (DAW)': [
        (
            'Single Page Applications (SPA) e Componentização',
            'Conceitos de SPAs, reatividade e hierarquia de componentes.',
        ),
        ('Gerenciamento de Estado Global', 'Store centralizada, actions, reducers e fluxo unidirecional de dados.'),
        ('Comunicação em Tempo Real e WebSockets', 'Protocolos bidirecionais, SSE (Server-Sent Events) e WebSockets.'),
        ('Progressive Web Apps (PWA) e Service Workers', 'Estratégias de cache offline, manifest e notificações push.'),
        (
            'Deploy, CI/CD e Infraestrutura Web',
            'Empacotamento com Docker, pipelines de integração contínua e hospedagem em nuvem.',
        ),
    ],
    'Estrutura de Dados (ESD)': [
        (
            'Vetores, Listas Encadeadas e Duplamente Encadeadas',
            'Alocação dinâmica, nós, ponteiros e operações de inserção/remoção.',
        ),
        ('Pilhas (Stack) e Filas (Queue)', 'Comportamento LIFO/FIFO, implementações estáticas e encadeadas.'),
        (
            'Tabelas Hash e Funções de Espalhamento',
            'Tratamento de colisões, encadeamento aberto e endereçamento direto.',
        ),
        (
            'Árvores Binárias de Busca (BST) e Árvores AVL',
            'Propriedades de busca, balanceamento automático e percursos (pré, in, pós).',
        ),
        ('Algoritmos de Ordenação Eficientes', 'Análise assintótica O(n log n): Quicksort, Mergesort e Heapsort.'),
        (
            'Grafos: Representação e Algoritmos de Busca',
            'Matriz e lista de adjacências, busca em largura (BFS) e busca em profundidade (DFS).',
        ),
    ],
    'Fundamentos de Algoritmos de Computação (FAC)': [
        (
            'Lógica de Programação e Tipos de Dados',
            'Conceito de algoritmo, variáveis, constantes e operadores aritméticos e lógicos.',
        ),
        (
            'Estruturas de Controle Condicional',
            'Desvios condicionais simples, compostos e encadeados (if-else, switch-case).',
        ),
        ('Estruturas de Repetição', 'Laços contados e condicionais (for, while, do-while) e controle de fluxo.'),
        ('Vetores e Matrizes', 'Manipulação de arrays unidimensionais e matrizes multidimensionais.'),
        (
            'Modularização e Escopo de Variáveis',
            'Procedimentos, funções, passagem de parâmetros por valor e referência.',
        ),
    ],
    'Fundamentos de Programação (FPR)': [
        (
            'Sintaxe da Linguagem C e Tipos Primitivos',
            'Estrutura de programas em C, entrada e saída formatada (printf/scanf).',
        ),
        (
            'Ponteiros e Aritmética de Ponteiros',
            'Endereços de memória, desreferenciação e relação entre vetores e ponteiros.',
        ),
        ('Alocação Dinâmica de Memória', 'Uso de malloc, calloc, realloc, free e prevenção de memory leaks.'),
        (
            'Manipulação de Strings e Arquivos',
            'Manipulação de buffers, arquivos texto e binários (fopen, fread, fwrite).',
        ),
        (
            'Estruturas Heterogêneas (Structs e Unions)',
            'Definição de tipos abstratos, typedef, structs aninhadas e vetores de structs.',
        ),
    ],
    'Utilização de Banco de Dados e SQL (UBD)': [
        (
            'Modelo Relacional e Álgebra Relacional',
            'Tabelas, tuplas, domínios, chaves primárias e integridade referencial.',
        ),
        ('Comandos DML: Consultas SQL e Filtros', 'SELECT, WHERE, operadores lógicos, ordenação e limites.'),
        ('Junções de Tabelas (JOINs)', 'INNER JOIN, LEFT JOIN, RIGHT JOIN, FULL OUTER JOIN e junções múltiplas.'),
        ('Funções Agregadas e Agrupamento', 'COUNT, SUM, AVG, MIN, MAX com cláusulas GROUP BY e HAVING.'),
        (
            'Subconsultas e Operadores de Conjunto',
            'Subqueries correlacionadas e não-correlacionadas, UNION, INTERSECT, EXCEPT.',
        ),
        (
            'Comandos DDL: Criação e Alteração de Esquemas',
            'CREATE TABLE, ALTER TABLE, DROP TABLE, restrições CHECK e UNIQUE.',
        ),
    ],
    'Projeto de Banco de Dados (PBD)': [
        (
            'Modelagem Conceitual (Diagrama E-R)',
            'Entidades fortes/fracas, atributos, relacionamentos e cardinalidades (1:1, 1:N, N:N).',
        ),
        (
            'Mapeamento Conceitual para Lógico Relacional',
            'Regras de transformação de diagramas E-R em esquemas de tabelas relacionais.',
        ),
        (
            'Teoria da Normalização de Dados',
            'Primeira, Segunda, Terceira Forma Normal (1FN, 2FN, 3FN) e Forma Normal de Boyce-Codd.',
        ),
        (
            'Transações e Propriedades ACID',
            'Atomicidade, Consistência, Isolamento e Durabilidade em SGBDs relacionais.',
        ),
        (
            'Indexação e Otimização de Consultas',
            'Árvores B/B+, índices clustered e non-clustered, análise de planos de execução.',
        ),
    ],
    'Programação de Scripts de Banco de Dados (SBD)': [
        (
            'Stored Procedures e Funções em PL/pgSQL / MySQL',
            'Blocos anônimos, variáveis, estruturas condicionais e funções armazenadas.',
        ),
        ('Gatilhos (Triggers) e Auditoria', 'Triggers BEFORE/AFTER de INSERT, UPDATE, DELETE e logs de auditoria.'),
        (
            'Views e Visões Materializadas',
            'Criação de visões lógicas, segurança de dados e visões materializadas para performance.',
        ),
        (
            'Cursores e Tratamento de Exceções em BD',
            'Manipulação de result sets linha a linha e blocos de tratamento de erros.',
        ),
        ('Bancos de Dados NoSQL', 'Modelos orientados a documentos, chave-valor, colunas largas e grafos.'),
    ],
    'Programação Orientada a Objetos Básica (POB)': [
        ('Conceitos Fundamentais de POO', 'Paradigma orientado a objetos vs procedural, classes, instâncias e estado.'),
        ('Encapsulamento e Modificadores de Acesso', 'Getters/Setters, visibilidade pública, privada e protegida.'),
        ('Herança e Reutilização de Código', 'Superclasses, subclasses, palavra-chave super e relação é-um.'),
        ('Polimorfismo e Sobrescrita de Métodos', 'Polimorfismo dinâmico, override de métodos e anotações.'),
        ('Tratamento de Exceções e Coleções', 'Blocos try-catch-finally, lançamento de exceções e uso de Lists/Maps.'),
    ],
    'Programação Orientada a Objetos Avançada (POA)': [
        ('Interfaces e Classes Abstratas', 'Contratos de software, acoplamento fraco e inversão de dependência.'),
        (
            'Padrões de Projeto Criacionais e Estruturais',
            'Factory Method, Singleton, Builder, Adapter, Facade e Decorator.',
        ),
        ('Padrões de Projeto Comportamentais', 'Observer, Strategy, Command e Template Method.'),
        (
            'Generics e Programação Funcional',
            'Tipos genéricos, Expressões Lambda, Streams API e interfaces funcionais.',
        ),
        (
            'Testes Automatizados e Princípios SOLID',
            'Testes unitários com mocks, asserções, cobertura e princípios SOLID.',
        ),
    ],
    'Engenharia de Requisitos (REQ)': [
        (
            'Fundamentos de Engenharia de Requisitos',
            'Definição, ciclo de vida dos requisitos e papel do analista de requisitos.',
        ),
        (
            'Técnicas de Elicitação de Requisitos',
            'Entrevistas, questionários, workshops, observação e análise de documentos.',
        ),
        (
            'Requisitos Funcionais e Não-Funcionais',
            'Classificação de requisitos (desempenho, usabilidade, segurança, portabilidade).',
        ),
        (
            'Modelagem de Casos de Uso e User Stories',
            'Diagramas de casos de uso, cenários, histórias de usuário com critérios de aceite.',
        ),
        (
            'Validação, Rastreabilidade e Gestão de Mudanças',
            'Matriz de rastreabilidade, prototipação rápida e controle de versões de requisitos.',
        ),
    ],
    'Modelagem de Sistemas (MOD)': [
        (
            'Introdução à UML e Modelagem Visual',
            'Papel dos modelos de software, vistas estruturais e comportamentais da UML.',
        ),
        (
            'Diagramas de Classes e Diagramas de Objetos',
            'Relacionamentos de associação, agregação, composição e dependência.',
        ),
        (
            'Diagramas de Sequência e Comunicação',
            'Linhas de vida, mensagens síncronas/assíncronas e fluxo de interação.',
        ),
        (
            'Diagramas de Atividades e Máquinas de Estados',
            'Modelagem de processos de negócio, bifurcações, junções e transições de estados.',
        ),
        (
            'Diagramas de Componentes e Implantação',
            'Mapeamento de artefatos de software para nós físicos e infraestrutura de hardware.',
        ),
    ],
    'Projeto de Sistemas (PJS)': [
        (
            'Padrões de Arquitetura de Software',
            'Arquitetura em camadas, MVC, MVVM, Microservices e arquitetura orientada a eventos.',
        ),
        (
            'Clean Architecture e Hexagonal Architecture',
            'Separação de responsabilidades, entidades de domínio, use cases e adaptadores.',
        ),
        (
            'Design de APIs e Contratos de Integração',
            'OpenAPI/Swagger, versionamento de APIs, DTOs e mapeamento de dados.',
        ),
        ('Persistência Poliglota e Cache', 'Integração de BDs relacionais com caches em memória (Redis/Memcached).'),
    ],
    'Gerência e Projeto de Sistemas (GPS)': [
        (
            'Metodologias Ágeis de Desenvolvimento',
            'Manifesto Ágil, framework Scrum (papéis, cerimônias e artefatos) e Kanban.',
        ),
        (
            'Gestão de Escopo, Cronograma e Estimativas',
            'Story Points, Planning Poker, estimativas paramétricas e cronogramas ágeis.',
        ),
        (
            'Gerenciamento de Riscos em Projetos de TI',
            'Identificação, análise qualitativa/quantitativa e planos de mitigação de riscos.',
        ),
        (
            'Controle de Qualidade e Métricas de Software',
            'Lead time, cycle time, velocity charts, dívida técnica e refatoração.',
        ),
    ],
    'Fundamentos de Sistemas Operacionais (SOP)': [
        (
            'Arquitetura de Sistemas Operacionais',
            'Modo núcleo/usuário, chamadas de sistema (syscalls) e estrutura de SOs.',
        ),
        (
            'Gerenciamento de Processos e Threads',
            'Estados de processos, PCB, criação de processos (fork/exec) e multithreading.',
        ),
        (
            'Algoritmos de Escalonamento de CPU',
            'FCFS, SJF, Round Robin, prioridades e escalonamento por múltiplas filas.',
        ),
        (
            'Sincronização de Processos e Deadlocks',
            'Região crítica, exclusão mútua, semáforos, mutexes e condições de Coffman.',
        ),
        (
            'Gerenciamento de Memória e Memória Virtual',
            'Paginação, segmentação, TLB, algoritmos de substituição de páginas (LRU/FIFO).',
        ),
        (
            'Sistemas de Arquivos e Armazenamento',
            'Inodes, estrutura de diretórios, alocação contígua/encadeada e sistemas RAID.',
        ),
    ],
    'Fundamentos de Redes e Sistemas Distribuídos (RSD)': [
        ('Modelos de Referência OSI e TCP/IP', 'Camadas, funções, encapsulamento e desencapsulamento de pacotes.'),
        (
            'Camada de Transporte (TCP e UDP)',
            'Controle de fluxo, controle de congestionamento, handshake de 3 vias e portas.',
        ),
        ('Camada de Rede e Roteamento IP', 'IPv4, IPv6, máscaras de sub-rede, CIDR, NAT e algoritmos de roteamento.'),
        (
            'Protocolos da Camada de Aplicação',
            'HTTP/HTTPS, DNS, DHCP, FTP, SMTP e arquiteturas cliente-servidor e P2P.',
        ),
        ('Conceitos de Sistemas Distribuídos', 'Transparência, escalabilidade, RPC, gRPC e consenso distribuído.'),
    ],
    'Segurança da Informação (SEG)': [
        (
            'Pilares da Segurança da Informação',
            'Confidencialidade, Integridade, Disponibilidade, Autenticidade e Não-repúdio.',
        ),
        (
            'Criptografia Simétrica e Assimétrica',
            'Algoritmos AES, DES, RSA, curvas elípticas e funções de hash (SHA-256).',
        ),
        (
            'Assinaturas Digitais e Certificados PKI',
            'Infraestrutura de Chaves Públicas, autoridades certificadoras e HTTPS/TLS.',
        ),
        (
            'Vulnerabilidades Web e OWASP Top 10',
            'Injeção SQL, Cross-Site Scripting (XSS), CSRF, quebra de controle de acesso.',
        ),
        (
            'Controle de Acesso e Gestão de Identidades',
            'RBAC, ABAC, autenticação multifator (MFA), OAuth 2.0 e OpenID Connect.',
        ),
    ],
    'Interface Homem-Máquina (IHM)': [
        (
            'Fundamentos de IHC e Fatores Humanos',
            'Processamento cognitivo humano, modelos mentais, percepção e ergonomia.',
        ),
        (
            'Heurísticas de Nielsen e Usabilidade',
            'As 10 heurísticas de usabilidade de Nielsen e avaliação heurística de interfaces.',
        ),
        (
            'Acessibilidade Digital e Padrões WCAG',
            'Princípios PERC (Perceptível, Operável, Compreensível, Robusto) e leitores de tela.',
        ),
        (
            'Design Centrado no Usuário e Prototipação',
            'Personas, jornadas do usuário, wireframes de baixa/alta fidelidade e testes de usabilidade.',
        ),
    ],
    'Organização de Computadores (ORG)': [
        (
            'Sistemas de Numeração e Representação de Dados',
            'Binário, octal, hexadecimal, complemento de 2 e ponto flutuante IEEE 754.',
        ),
        (
            'Álgebra Booleana e Portas Lógicas',
            'Tabelas verdade, teoremas de De Morgan, mapas de Karnaugh e circuitos combinacionais.',
        ),
        (
            'Arquitetura de Processadores (CPU)',
            'Registradores, ULA, unidade de controle, pipeline de instruções e arquiteturas RISC/CISC.',
        ),
        (
            'Hierarquia de Memória e Memória Cache',
            'Mapeamento direto, associativo por conjunto, políticas de escrita (write-through/back).',
        ),
        ('Barramentos e Dispositivos de E/S', 'Interrupções, DMA (Direct Memory Access) e barramentos de comunicação.'),
    ],
    'Programação de Dispositivos Móveis (PDM)': [
        (
            'Fundamentos de Desenvolvimento Mobile',
            'Ciclo de vida de aplicações móveis, componentes essenciais (Activities/Fragments).',
        ),
        (
            'Layouts e Interfaces Responsivas Mobile',
            'Design adaptativo, constraint layouts, componentes de UI para telas sensíveis ao toque.',
        ),
        (
            'Persistência de Dados Local em Dispositivos Móveis',
            'SQLite, Room Persistence, SharedPreferences e armazenamento em arquivo.',
        ),
        (
            'Integração com APIs e Consumo de Dados Remotos',
            'Bibliotecas HTTP, parsing JSON assíncrono e tratamento de conectividade.',
        ),
        (
            'Recursos Nativos e Sensores de Hardware',
            'Geolocalização, GPS, câmera, acelerômetro e gerenciamento de permissões dinâmicas.',
        ),
    ],
    'Álgebra (ALG)': [
        (
            'Matrizes, Operações e Determinantes',
            'Adição, multiplicação, transposição, inversa e determinantes via Laplace.',
        ),
        ('Sistemas de Equações Lineares', 'Método de Eliminação de Gauss, escalonamento e classificação de sistemas.'),
        (
            'Espaços e Subespaços Vetoriais',
            'Combinação linear, dependência e independência linear (LI/LD), bases e dimensão.',
        ),
        ('Transformações Lineares', 'Definição, núcleo, imagem, matriz associada e teoremas de dimensão.'),
        (
            'Autovalores e Autovetores',
            'Polinômio característico, diagonalização de matrizes e aplicações computacionais.',
        ),
    ],
    'Cálculo (CAL)': [
        ('Funções Reais e Limites', 'Conceito intuitivo e formal de limites, limites laterais e indeterminações.'),
        ('Continuidade de Funções', 'Propriedades de funções contínuas e Teorema do Valor Intermediário.'),
        ('Derivadas e Regras de Derivação', 'Taxa de variação instantânea, regra da cadeia, derivação implícita.'),
        (
            'Aplicações de Derivadas',
            'Máximos e mínimos, testes de concavidade, problemas de otimização e taxas relacionadas.',
        ),
        (
            'Integrais e Teorema Fundamental do Cálculo',
            'Antiderivadas, integração por substituição, integração por partes e cálculo de áreas.',
        ),
    ],
    'Estatística e Probabilidade (EST)': [
        (
            'Estatística Descritiva e Medidas de Tendência Central',
            'Média aritmética, mediana, moda e interpretação de distribuições de frequência.',
        ),
        (
            'Medidas de Dispersão e Variabilidade',
            'Variância, desvio-padrão, coeficiente de variação e quartis (Boxplot).',
        ),
        (
            'Teoria das Probabilidades',
            'Espaço amostral, eventos, probabilidade condicional, independência e Teorema de Bayes.',
        ),
        (
            'Distribuições de Probabilidade Discretas e Contínuas',
            'Distribuição Binomial, Poisson e Distribuição Normal (Gaussiana).',
        ),
        (
            'Inferência Estatística e Intervalos de Confiança',
            'Amostragem, Teorema do Limite Central, testes de hipóteses e p-valor.',
        ),
    ],
    'Matemática para Computação (MAC)': [
        (
            'Lógica Proposicional e de Primeira Ordem',
            'Conectivos, tabelas-verdade, tautologias, equivalências e quantificadores (∀, ∃).',
        ),
        (
            'Métodos de Prova e Indução Matemática',
            'Prova direta, por contradição, contrapositiva e princípio da indução finita.',
        ),
        (
            'Teoria dos Conjuntos e Relações',
            'Operações com conjuntos, relações de equivalência, ordens parciais e funções.',
        ),
        (
            'Teoria dos Grafos Aplicada à Computação',
            'Grafos simples, direcionados, árvores, planaridade e coloração de grafos.',
        ),
        ('Análise Combinatória e Contagem', 'Princípio fundamental da contagem, permutações, arranjos e combinações.'),
    ],
    'Matemática Básica (MAB)': [
        (
            'Conjuntos Numéricos e Operações Elementares',
            'Números naturais, inteiros, racionais, reais e propriedades operatórias.',
        ),
        ('Razão, Proporção e Regra de Três', 'Proporcionalidade direta e inversa, porcentagem e juros simples.'),
        (
            'Equações e Inequações de 1º e 2º Grau',
            'Resolução de equações lineares e quadráticas, fórmula de Bhaskara e gráficos.',
        ),
        (
            'Funções Exponenciais e Logarítmicas',
            'Propriedades dos expoentes, logaritmos e resolução de equações exponenciais.',
        ),
    ],
    'Língua Portuguesa (LPO)': [
        (
            'Compreensão e Interpretação de Textos Técnicos',
            'Leitura crítica, identificação de tese, argumentos e coesão textual.',
        ),
        (
            'Sintaxe da Oração e do Período',
            'Termos essenciais, integrantes e acessórios da oração, regência e concordância verbal/nominal.',
        ),
        (
            'Redação Técnica e Comunicação Profissional',
            'Clareza, objetividade, coesão, coerência e elaboração de relatórios técnicos.',
        ),
    ],
    'Língua Estrangeira (LES)': [
        (
            'Leitura e Interpretação de Documentação Técnica em Inglês',
            'Vocabulário técnico de TI, falsos cognatos e estratégias de leitura (skimming/scanning).',
        ),
        (
            'Estruturas Gramaticais para Textos Técnicos',
            'Tempos verbais, voz passiva, imperativo em manuais e conectivos em documentações de API.',
        ),
    ],
    'Metodologia da Pesquisa (MET)': [
        (
            'Método Científico e Tipos de Pesquisa',
            'Pesquisa quantitativa, qualitativa, exploratória, descritiva e experimental em computação.',
        ),
        (
            'Normas ABNT e Estrutura de Trabalhos Acadêmicos',
            'Citações, referências bibliográficas, estrutura de monografias e artigos científicos.',
        ),
        (
            'Ética na Pesquisa e Escrita Científica',
            'Plágio, propriedade intelectual, redação de resumos e palavras-chave.',
        ),
    ],
    'Empreendedorismo e Inovação (EMP)': [
        (
            'Cultura Empreendedora e Modelos de Negócio',
            'Identificação de oportunidades, modelo Lean Canvas e proposta de valor.',
        ),
        (
            'Startups, MVP e Metodologia Lean',
            'Validação de hipóteses, Produto Mínimo Viável (MVP) e pivotagem de negócios de tecnologia.',
        ),
        (
            'Propriedade Intelectual e Financiamento',
            'Patentes, registro de software, venture capital e captação de recursos.',
        ),
    ],
    'Técnicas e Paradigmas Humanos (TPH)': [
        (
            'Comunicação Interpessoal e Trabalho em Equipe',
            'Dinâmicas de grupo, empatia, escuta ativa e resolução colaborativa de conflitos.',
        ),
        (
            'Ética Profissional e Cidadania Digital',
            'Código de ética do profissional de TI, privacidade de dados e impacto social da tecnologia.',
        ),
    ],
    'Métodos e Processos Administrativos (MPA)': [
        (
            'Teorias da Administração e Organização Empresarial',
            'Estruturas organizacionais, planejamento estratégico, tático e operacional.',
        ),
        (
            'Gestão de Processos de Negócio (BPM)',
            'Mapeamento, modelagem e otimização de fluxos de processos organizacionais.',
        ),
    ],
    'Introdução à Análise de Sistemas (IAS)': [
        (
            'Conceitos de Sistemas de Informação',
            'Entrada, processamento, saída, feedback e componentes de sistemas corporativos.',
        ),
        (
            'Ciclo de Vida do Desenvolvimento de Software (SDLC)',
            'Modelos cascata, iterativo, espiral e ágil no ciclo de vida de software.',
        ),
    ],
}


class Command(BaseCommand):
    help = 'Popula tópicos curriculares para as disciplinas ativas da FAETERJ-Rio'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limpar',
            action='store_true',
            help='Remove tópicos existentes antes de recriar a base de tópicos',
        )

    def handle(self, *args, **options):
        limpar = options.get('limpar', False)

        self.stdout.write(self.style.NOTICE('Iniciando seed de tópicos curriculares da FAETERJ-Rio...'))

        with transaction.atomic():
            if limpar:
                total_removidos = Topico.objects.count()
                Topico.objects.all().delete()
                self.stdout.write(self.style.WARNING(f'Removidos {total_removidos} tópicos existentes.'))

            total_criados = 0
            total_existentes = 0

            for disc_nome_pattern, topicos in TOPICOS_DATA.items():
                # Busca por nome exato ou contenção
                disc = Disciplina.objects.filter(nome__icontains=disc_nome_pattern.split(' (')[0]).first()
                if not disc and '(' in disc_nome_pattern:
                    sigla = disc_nome_pattern.split('(')[-1].replace(')', '').strip()
                    disc = Disciplina.objects.filter(nome__icontains=sigla).first()

                if not disc:
                    self.stdout.write(
                        self.style.WARNING(f'Disciplina não encontrada para o padrão: {disc_nome_pattern}')
                    )
                    continue

                for nome_topico, desc_topico in topicos:
                    topico, created = Topico.objects.get_or_create(
                        disciplina=disc,
                        nome=nome_topico,
                        defaults={'descricao': desc_topico, 'ativo': True},
                    )
                    if created:
                        total_criados += 1
                    else:
                        total_existentes += 1

            # Associa questões órfãs aos tópicos correspondentes da disciplina caso estejam sem tópico
            questoes_sem_topico = Questao.objects.filter(topico__isnull=True)
            total_questoes_atualizadas = 0

            for questao in questoes_sem_topico:
                topico_padrao = Topico.objects.filter(disciplina=questao.disciplina).first()
                if topico_padrao:
                    questao.topico = topico_padrao
                    questao.save(update_fields=['topico'])
                    total_questoes_atualizadas += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Seed concluída com sucesso! {total_criados} tópicos novos criados, '
                f'{total_existentes} já existentes. Total geral no banco: {Topico.objects.count()} tópicos. '
                f'({total_questoes_atualizadas} questões associadas a tópicos)'
            )
        )
