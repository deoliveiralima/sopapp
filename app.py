from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from rdflib import Graph, Literal, Namespace, RDF, URIRef, XSD, RDFS
import uuid
import hashlib
import requests
import re
import unicodedata

app = Flask(__name__)
CORS(app)

# 1. Namespaces (Conforme sua ontologia pop_turtle.ttl)
SOP = Namespace("https://purl.archive.org/sopontology#")
ORG = Namespace("http://www.w3.org/ns/org#")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")
BASE = Namespace("http://exemplo.org/iff#")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")

# --- FUNÇÕES AUXILIARES ---
# --- FUNÇÃO AUXILIAR ---
def add_step_triples(g, step_uri, s_data, idx, parent_uri):
    # Instanciação básica do Step
    g.add((step_uri, RDF.type, SOP.Step))
    g.add((step_uri, SOP.name, Literal(s_data['name'], datatype=XSD.string)))
    g.add((step_uri, SOP.discriminator, Literal(idx, datatype=XSD.integer)))
    
    # Define a hierarquia (O "pai" pode ser o POP ou uma Seção)
    g.add((parent_uri, SOP.hasStep, step_uri))
    
    # Se for um Sub-POP ou Etapa Reutilizada (link via rdfs:seeAlso)
    if s_data.get('uri'):
        g.add((step_uri, RDFS.seeAlso, URIRef(s_data['uri'])))
        # Marca também como Sop se for um sub-processo formal
        if s_data.get('isSubPop'):
            g.add((step_uri, RDF.type, SOP.Sop))
    
    # Processa Executor (performer) e Local (place) específicos da etapa
    for key, prop, prefix in [('performer', SOP.performedBy, 'perf'), ('place', SOP.performedAt, 'place')]:
        obj = s_data.get(key, {})
        if obj.get('name'):
            obj_uri = URIRef(obj['uri']) if obj.get('uri') else BASE[f"{prefix}_{generate_stable_id(obj['name'])}"]
            if not obj.get('uri'):
                g.add((obj_uri, RDF.type, SOP[obj.get('type', 'Performer' if prefix == 'perf' else 'Place')]))
                g.add((obj_uri, RDFS.label, Literal(obj['name'])))
            g.add((step_uri, prop, obj_uri))

    # --- NOVO: Captura as condições (Pre/Pos) enviadas pelo JS ---
    logic = s_data.get('logic', {})
    if logic.get('preCondition'):
        g.add((step_uri, SOP.preCondition, Literal(logic['preCondition'], datatype=XSD.string)))
    if logic.get('posCondition'):
        g.add((step_uri, SOP.posCondition, Literal(logic['posCondition'], datatype=XSD.string)))

def generate_stable_id(text):
    """Gera um ID curto e único baseado no texto para manter URIs estáveis."""
    if not text: return str(uuid.uuid4())[:8]
    return hashlib.md5(text.strip().lower().encode()).hexdigest()[:8]

def slugify(text):

 
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '_', text)
    return text

    

def upload_to_graphdb(rdf_data, repo_id="Dissertacao_SOP"):
    """Envia os dados diretamente para o repositório do GraphDB via API."""
    url = f"http://localhost:7200/repositories/{repo_id}/statements"
    headers = {"Content-Type": "text/turtle"}
    try:
        response = requests.post(url, data=rdf_data, headers=headers)
        if 200 <= response.status_code < 300:
            print(f"Sucesso: Dados integrados ao GraphDB ({response.status_code})")
            return True
        return False
    except Exception as e:
        print(f"Falha na conexão com GraphDB: {e}")
        return False

# --- ROTAS ---

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/api/save-rdf', methods=['POST'])
def save_rdf():
    data = request.json
    g = Graph()
    g.bind("sop", SOP); g.bind("org", ORG); g.bind("foaf", FOAF); g.bind("base", BASE); g.bind("rdfs", RDFS); g.bind("skos", SKOS)

    # --- A. INSTANCIAR O POP (SOP) ---
    metadata = data.get('metadata', {})
    pop_name_raw = metadata.get('name', 'sem_nome')
    pop_number_raw = metadata.get('number', '000')
    pop_version_raw = metadata.get('version', '1.0') 
    
    pop_slug = slugify(pop_name_raw)
    num_slug = slugify(str(pop_number_raw))
    version_slug = slugify(pop_version_raw).replace("_", "-")
    
    pop_id = f"{pop_slug}_{num_slug}_{version_slug}"
    pop_uri = BASE[pop_id] 

    g.add((pop_uri, RDF.type, SOP.Sop))
    g.add((pop_uri, SOP.name, Literal(metadata.get('name', ''), datatype=XSD.string)))
    g.add((pop_uri, SOP.version, Literal(metadata.get('version', ''), datatype=XSD.string)))
    g.add((pop_uri, SOP.description, Literal(metadata.get('description', ''), datatype=XSD.string)))
    
    # Datas
    dates = metadata.get('dates', {})
    if dates.get('creation'):
        g.add((pop_uri, SOP.creationDate, Literal(dates['creation'], datatype=XSD.dateTime)))
    if dates.get('approval'):
        g.add((pop_uri, SOP.approvalDate, Literal(dates['approval'], datatype=XSD.date)))
    
    if metadata.get('status'):
        g.add((pop_uri, SOP.hasStatus, SOP[metadata.get('status')]))

    # --- CLASSIFICAÇÕES E TERMOS ---
    for class_name in metadata.get('classifications', []):
        if class_name:
            class_uri = BASE[f"concept_{generate_stable_id(class_name)}"]
            g.add((class_uri, RDF.type, SKOS.Concept))
            g.add((class_uri, SKOS.prefLabel, Literal(class_name, datatype=XSD.string)))
            g.add((pop_uri, SOP.hasClassification, class_uri))

    for t in metadata.get('terms', []):
        if t.get('name'):
            term_uri = BASE[f"term_{generate_stable_id(t['name'])}"]
            g.add((term_uri, RDF.type, SKOS.Concept))
            g.add((term_uri, SKOS.prefLabel, Literal(t['name'], datatype=XSD.string)))
            if t.get('definition'):
                g.add((term_uri, SKOS.definition, Literal(t['definition'], datatype=XSD.string)))
            g.add((pop_uri, SOP['hasTerm'], term_uri))

    # Responsáveis (Organizações)
    for org_uri in metadata.get('responsible_uris', []):
        if org_uri: g.add((pop_uri, SOP.hasResponsible, URIRef(org_uri)))

    # --- NOVO: EXECUTOR E LOCAL DO POP (ACTIVITY) ---
    # Buscamos os dados que o JS enviou na raiz do objeto 'data'
    general_perf = data.get('general_performer', {})
    general_place = data.get('general_place', {})

    for obj, prop, prefix in [(general_perf, SOP.performedBy, 'perf'), 
                               (general_place, SOP.performedAt, 'place')]:
        if obj.get('name'):
            if obj.get('uri'):
                obj_uri = URIRef(obj['uri'])
            else:
                obj_id = generate_stable_id(obj['name'])
                obj_uri = BASE[f"{prefix}_{obj_id}"]
                g.add((obj_uri, RDF.type, SOP[obj['type']]))
                g.add((obj_uri, RDFS.label, Literal(obj['name'])))
            
            # Vincula o Executor/Local ao POP (como ele é uma Activity na sua ontologia)
            g.add((pop_uri, prop, obj_uri))

    # --- B. AGENTES (AUTORIA) ---
    agents_data = data.get('agents', {})
    mapping = [('creators', SOP.createdBy), ('checkers', SOP.checkedBy), ('approvers', SOP.approvedBy)]
    
    for key, predicate in mapping:
        for agent in agents_data.get(key, []):
            if agent.get('name'):
                agent_uri = URIRef(agent['uri']) if agent.get('uri') else BASE[f"agent_{generate_stable_id(agent['name'])}"]
                if not agent.get('uri'):
                    g.add((agent_uri, RDF.type, FOAF[agent['type']])) 
                    g.add((agent_uri, FOAF.name, Literal(agent['name'])))
                g.add((pop_uri, predicate, agent_uri))

# --- C. HIERARQUIA: SEÇÕES E ETAPAS ---
    sections_data = data.get('sections', [])
    
    if sections_data:
        # Cenário A: O POP possui divisões em Seções
        for i, sec in enumerate(sections_data):
            sec_idx = i + 1
            sec_uri = BASE[f"section_{pop_id}_{sec_idx}"]
            
            g.add((sec_uri, RDF.type, SOP.Section))
            g.add((sec_uri, SOP.name, Literal(sec['name'], datatype=XSD.string)))
            g.add((sec_uri, SOP.discriminator, Literal(sec_idx, datatype=XSD.integer)))
            
            # Liga o POP à Seção (hasSection)
            g.add((pop_uri, SOP.hasSection, sec_uri))
            
            # Processa os passos pertencentes a esta seção específica
            for j, s in enumerate(sec.get('steps', [])):
                step_idx = j + 1
                # URI estruturada para garantir exclusividade: step_POPID_SECID_STEPID
                step_uri = BASE[f"step_{pop_id}_{sec_idx}_{step_idx}"]
                
                # Chama a lógica comum de criação de triplos para o Step
                add_step_triples(g, step_uri, s, step_idx, sec_uri)
    else:
        # Cenário B: Mantém compatibilidade com POPs de lista plana (sem seções)
        for i, s in enumerate(data.get('steps', [])):
            step_idx = i + 1
            step_uri = BASE[f"step_{pop_id}_{step_idx}"]
            add_step_triples(g, step_uri, s, step_idx, pop_uri)

    # --- D. ITENS DO POP ---
    for item in data.get('items', []):
        item_uri = URIRef(item['uri']) if item.get('uri') else BASE[f"item_{generate_stable_id(item['type'] + item['name'])}"]
        if not item.get('uri'):
            g.add((item_uri, RDF.type, SOP[item['type']]))
            g.add((item_uri, SOP.name, Literal(item['name'])))
        g.add((item_uri, SOP.discriminator, Literal(item['discriminator'], datatype=XSD.integer)))
        g.add((pop_uri, SOP.hasItem, item_uri))

    # Serialização e Finalização
    rdf_content = g.serialize(format="turtle")
    filename = f"{pop_id}.ttl"
    with open(filename, "w", encoding="utf-8") as f: 
        f.write(rdf_content)
    
    # Se você tiver a função upload_to_graphdb funcionando:
    # upload_to_graphdb(rdf_content)

    return jsonify({"message": "RDF gerado e enviado com sucesso!", "file": filename}), 200

# Adicione esta função auxiliar ao seu app.py
def query_graphdb(sparql_query, repo_id="Dissertacao_SOP"):
    url = f"http://localhost:7200/repositories/{repo_id}"
    headers = {"Accept": "application/sparql-results+json"}
    try:
        response = requests.get(url, params={'query': sparql_query}, headers=headers)
        return response.json() if response.status_code == 200 else None
    except Exception as e:
        print(f"Erro na consulta SPARQL: {e}")
        return None

# --- NOVAS ROTAS DE PÁGINAS ---
@app.route('/list')
def list_page():
    return send_from_directory('.', 'list.html')

@app.route('/view/<pop_id>')
def view_page(pop_id):
    return send_from_directory('.', 'view.html')

# --- NOVAS ROTAS DE API ---
@app.route('/api/pops', methods=['GET'])
def get_all_pops():
    # Usamos o prefixo correto com # conforme sua ontologia
    sparql = """
    PREFIX sop: <https://purl.archive.org/sopontology#>
    SELECT ?id ?name ?hasStatus WHERE {
        ?pop a sop:Sop ;
             sop:name ?name .
        OPTIONAL { ?pop sop:hasStatus ?s . BIND(STRAFTER(STR(?s), "#") AS ?hasStatus) }
        # Pega o ID final da URI independente do prefixo
        BIND(REPLACE(STR(?pop), "^.*[/#]", "") AS ?id)
    } ORDER BY ?name
    """
    result = query_graphdb(sparql)
    return jsonify(result['results']['bindings'] if result else [])



@app.route('/api/pop/<pop_id>', methods=['GET'])
def get_pop_details(pop_id):
    uri = f"<{BASE}{pop_id}>"
    
    data = {
        "metadata": {"performer": "Institucional", "place": "Não definido"},
        "agents": {"responsible": set(), "creators": set(), "checkers": set(), "approvers": set()},
        "concepts": {"classifications": set(), "terms": []},
        "items": [],
        "steps": []
    }

    # 1. Metadados e Agentes (Deduplicação com set)
    sparql_meta = f"""
    PREFIX sop: <https://purl.archive.org/sopontology#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX foaf: <http://xmlns.com/foaf/0.1/>
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

    SELECT ?p ?o ?label WHERE {{
        {uri} ?p ?o .
        OPTIONAL {{ ?o sop:name ?n }}
        OPTIONAL {{ ?o rdfs:label ?rl }}
        OPTIONAL {{ ?o foaf:name ?fn }}
        OPTIONAL {{ ?o skos:prefLabel ?sl }}
        BIND(COALESCE(?n, ?rl, ?fn, ?sl, STR(?o)) AS ?label)
        FILTER(?p NOT IN (sop:hasItem, sop:hasStep, sop:hasTerm))
    }}
    """
    res_meta = query_graphdb(sparql_meta)
    if res_meta:
        for row in res_meta['results']['bindings']:
            p = row['p']['value']
            label = row.get('label', {}).get('value', row['o']['value'])
            
            if p == str(SOP.name): data["metadata"]["name"] = label
            elif p == str(SOP.version): data["metadata"]["version"] = label
            elif p == str(SOP.description): data["metadata"]["description"] = label
            elif p == str(SOP.hasStatus): data["metadata"]["status"] = label.split("#")[-1].upper()
            elif p == str(SOP.performedBy): data["metadata"]["performer"] = label
            elif p == str(SOP.performedAt): data["metadata"]["place"] = label
            elif p == str(SOP.hasResponsible): data["agents"]["responsible"].add(label)
            elif p == str(SOP.createdBy): data["agents"]["creators"].add(label)
            elif p == str(SOP.checkedBy): data["agents"]["checkers"].add(label)
            elif p == str(SOP.approvedBy): data["agents"]["approvers"].add(label)
            elif p == str(SOP.hasClassification): data["concepts"]["classifications"].add(label)

    # --- 2. Itens do POP (Deduplicação por URI e filtro de classe) ---
    sparql_items = f"""
    PREFIX sop: <https://purl.archive.org/sopontology#>
    SELECT DISTINCT ?item ?name ?disc ?type WHERE {{
        {uri} sop:hasItem ?item .
        ?item sop:name ?name ; 
              sop:discriminator ?disc ; 
              a ?type_uri .
        BIND(STRAFTER(STR(?type_uri), "#") AS ?type)
        # Mudar "SopItem" para "Item"
        FILTER(?type NOT IN ("Entity", "Item", "Activity", "NamedIndividual", "Thing", "Step"))
    }} ORDER BY ?disc
    """
    res_items = query_graphdb(sparql_items)

    # NOVO BLOCO: Busca os termos associados ao POP
    sparql_terms = f"""
    PREFIX sop: <https://purl.archive.org/sopontology#>
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    SELECT ?label ?def WHERE {{
        {uri} sop:hasTerm ?term .
        ?term skos:prefLabel ?label .
        OPTIONAL {{ ?term skos:definition ?def }}
    }}
    """
    res_terms = query_graphdb(sparql_terms)
    if res_terms:
        for row in res_terms['results']['bindings']:
            data["concepts"]["terms"].append({
                "name": row['label']['value'],
                "def": row.get('def', {}).get('value', 'Sem definição disponível')
            })
    
    # Dicionário temporário para evitar duplicar o mesmo item (chave é a URI do item)
    unique_items = {}

    if res_items:
        for row in res_items['results']['bindings']:
            item_uri = row['item']['value']
            item_name = row.get('name', {}).get('value', "Item sem nome")
            item_type = row.get('type', {}).get('value', "Outros")
            item_disc = int(row.get('disc', {}).get('value', 0))

            # Se o item ainda não foi processado, ou se o tipo atual for mais específico que "Outros"
            if item_uri not in unique_items or (unique_items[item_uri]['type'] == "Outros" and item_type != "Outros"):
                unique_items[item_uri] = {
                    "name": item_name,
                    "type": item_type,
                    "order": item_disc
                }

    # Transfere os itens únicos para a lista final
    data["items"] = list(unique_items.values())

    # 3. Etapas e Links para Sub-POPs (Activity Hierarchy)
    sparql_steps = f"""
    PREFIX sop: <https://purl.archive.org/sopontology#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

    SELECT ?secName ?secDisc ?step ?stepName ?stepDisc ?subPop WHERE {{
        {{
            # Caso o POP tenha seções
            {uri} sop:hasSection ?section .
            ?section sop:name ?secName ;
                     sop:discriminator ?secDisc .
            ?section sop:hasStep ?step .
        }} UNION {{
            # Caso o POP tenha passos diretos
            {uri} sop:hasStep ?step .
            BIND("Procedimento" AS ?secName)
            BIND(0 AS ?secDisc)
        }}
        ?step sop:name ?stepName ;
              sop:discriminator ?stepDisc .
        OPTIONAL {{ ?step rdfs:seeAlso ?subPop }}
    }} ORDER BY ?secDisc ?stepDisc
    """
    res_steps = query_graphdb(sparql_steps)
    if res_steps:
        for row in res_steps['results']['bindings']:
            data["steps"].append({
                "id": row.get('subPop', row['step'])['value'].split("/")[-1].split("#")[-1],
                "name": row['name']['value'],
                "isSubPop": "subPop" in row,
                "order": row['disc']['value'],
                # Adiciona o executor e local ao dicionário de cada etapa
                "performer": row.get('perfLabel', {}).get('value', 'Padrão'),
                "place": row.get('placeLabel', {}).get('value', 'Padrão')
            })

    # Converte sets para listas e limpa os dados
    for k in data["agents"]: 
        data["agents"][k] = list(set(data["agents"][k])) # Remove duplicatas caso existam
    
    data["concepts"]["classifications"] = list(set(data["concepts"]["classifications"]))
    
    return jsonify(data)
# --- ESTA ROTA DEVE FICAR ANTES DO IF __NAME__ ---
@app.route('/api/organizations', methods=['GET'])
def get_organizations():
    sparql = """
    PREFIX org: <http://www.w3.org/ns/org#>
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?uri ?name WHERE {
        { ?uri a org:FormalOrganization } UNION { ?uri a org:OrganizationalUnit }
        OPTIONAL { ?uri skos:prefLabel ?name }
        OPTIONAL { ?uri rdfs:label ?name }
        FILTER(bound(?name))
    } ORDER BY ?name
    """
    result = query_graphdb(sparql)
    return jsonify(result['results']['bindings'] if result else [])
@app.route('/api/classifications', methods=['GET'])
def get_classifications():
    # Retorna conceitos que JÁ foram usados no predicado sop:hasClassification
    sparql = """
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    PREFIX sop: <https://purl.archive.org/sopontology#>
    SELECT DISTINCT ?uri ?name WHERE {
        ?pop sop:hasClassification ?uri .
        ?uri skos:prefLabel ?name .
    } ORDER BY ?name
    """
    result = query_graphdb(sparql)
    return jsonify(result['results']['bindings'] if result else [])

@app.route('/api/terms', methods=['GET'])
def get_terms():
    # Retorna conceitos que JÁ foram usados no predicado sop:term
    sparql = """
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    PREFIX sop: <https://purl.archive.org/sopontology#>
    SELECT DISTINCT ?uri ?name ?definition WHERE {
        ?pop sop:hasTerm ?uri .
        ?uri skos:prefLabel ?name .
        OPTIONAL { ?uri skos:definition ?definition }
    } ORDER BY ?name
    """
    result = query_graphdb(sparql)
    return jsonify(result['results']['bindings'] if result else [])
@app.route('/api/agents', methods=['GET'])
def get_agents():
    sparql = """
    PREFIX foaf: <http://xmlns.com/foaf/0.1/>
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

    # Usamos SAMPLE ou MAX para pegar apenas um rótulo de tipo por URI
    SELECT ?uri ?name (MAX(?type_label) AS ?type) WHERE { 
        ?uri rdf:type/rdfs:subClassOf* foaf:Agent .
        
        # Busca o nome em diferentes propriedades
        { ?uri foaf:name ?name } UNION 
        { ?uri rdfs:label ?name } UNION 
        { ?uri skos:prefLabel ?name }

        # Busca o tipo, mas vamos filtrar para pegar algo mais específico que apenas 'Agent'
        ?uri rdf:type ?type_uri .
        BIND(REPLACE(STR(?type_uri), "^.*[/#]", "") AS ?type_label)
        
        # Filtro opcional: evita que o tipo mostrado seja apenas 'Agent' se houver outro mais específico
        FILTER(?type_label != "Agent")
        FILTER(!ISIRI(?name))
    } 
    GROUP BY ?uri ?name
    ORDER BY ?name
    """  
    result = query_graphdb(sparql)
    return jsonify(result['results']['bindings'] if result else [])

@app.route('/api/performers', methods=['GET'])
def get_performers():
    # Mapeia papéis e organizações para as classes da SOP Ontology
    sparql = """
    PREFIX org: <http://www.w3.org/ns/org#>
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT DISTINCT ?uri ?name ?type WHERE {
        { ?uri a org:Role . BIND("RolePerformer" AS ?type) }
        UNION
        { ?uri a org:Organization . BIND("OrganizationPerformer" AS ?type) }
        UNION
        { ?uri a org:OrganizationalUnit . BIND("OrganizationPerformer" AS ?type) }
        OPTIONAL { ?uri skos:prefLabel ?name }
        OPTIONAL { ?uri rdfs:label ?name }
        FILTER(bound(?name))
    } ORDER BY ?name
    """
    result = query_graphdb(sparql)
    return jsonify(result['results']['bindings'] if result else [])

@app.route('/api/places', methods=['GET'])
def get_places():
    # Mapeia locais físicos e setores para as classes da SOP Ontology
    sparql = """
    PREFIX org: <http://www.w3.org/ns/org#>
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT DISTINCT ?uri ?name ?type WHERE {
        { ?uri a org:Site . BIND("SitePlace" AS ?type) }
        UNION
        { ?uri a org:Organization . BIND("OrganizationPlace" AS ?type) }
        UNION
        { ?uri a org:OrganizationalUnit . BIND("OrganizationPlace" AS ?type) }
        OPTIONAL { ?uri skos:prefLabel ?name }
        OPTIONAL { ?uri rdfs:label ?name }
        FILTER(bound(?name))
    } ORDER BY ?name
    """
    result = query_graphdb(sparql)
    return jsonify(result['results']['bindings'] if result else [])

@app.route('/api/existing-steps', methods=['GET'])
def get_existing_steps():
    # Busca etapas, seus executores e locais originais
    sparql = """
    PREFIX sop: <https://purl.archive.org/sopontology#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT DISTINCT ?uri ?name ?perfName ?perfType ?placeName ?placeType WHERE {
        ?uri a sop:Step ;
             sop:name ?name .
        OPTIONAL { 
            ?uri sop:performedBy ?p . 
            ?p rdfs:label ?perfName . 
            ?p a ?pt . BIND(STRAFTER(STR(?pt), "#") AS ?perfType) 
        }
        OPTIONAL { 
            ?uri sop:performedAt ?l . 
            ?l rdfs:label ?placeName . 
            ?l a ?lt . BIND(STRAFTER(STR(?lt), "#") AS ?placeType) 
        }
    } ORDER BY ?name
    """
    result = query_graphdb(sparql)
    return jsonify(result['results']['bindings'] if result else [])

@app.route('/api/approved-pops', methods=['GET'])
def get_approved_pops():
    sparql = """
    PREFIX sop: <https://purl.archive.org/sopontology#>
    SELECT DISTINCT ?uri ?name ?id WHERE {
        ?uri a sop:Sop ;
             sop:name ?name ;
             sop:hasStatus sop:approved .
        # Extrai o ID da URI para linkagem
        BIND(REPLACE(STR(?uri), "^.*[/#]", "") AS ?id)
    } ORDER BY ?name
    """
    result = query_graphdb(sparql)
    return jsonify(result['results']['bindings'] if result else [])
# A rota /api/pops que já criamos servirá para incluir POPs como etapa.

@app.route('/api/sop-items', methods=['GET'])
def get_sop_items():
    # Busca instâncias de SopItem e suas subclasses
    sparql = """
    PREFIX sop: <https://purl.archive.org/sopontology#>
    SELECT DISTINCT ?uri ?name ?type WHERE {
        ?uri a ?type_uri .
        ?uri sop:name ?name .
        ?type_uri rdfs:subClassOf* sop:Item .
        BIND(STRAFTER(STR(?type_uri), "#") AS ?type)
    } ORDER BY ?name
    """
    result = query_graphdb(sparql)
    return jsonify(result['results']['bindings'] if result else [])
if __name__ == '__main__':
    app.run(debug=True, port=5000)  

