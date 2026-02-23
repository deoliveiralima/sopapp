from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from rdflib import Graph, Literal, Namespace, RDF, URIRef, XSD, RDFS
import uuid
import hashlib
import requests

app = Flask(__name__)
CORS(app)

# 1. Namespaces (Conforme sua ontologia pop_turtle.ttl)
SOP = Namespace("https://purl.archive.org/sopontology#")
ORG = Namespace("http://www.w3.org/ns/org#")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")
BASE = Namespace("http://iff.edu.br/saeg/sopontology/")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")

# --- FUNÇÕES AUXILIARES ---

def generate_stable_id(text):
    """Gera um ID curto e único baseado no texto para manter URIs estáveis."""
    if not text: return str(uuid.uuid4())[:8]
    return hashlib.md5(text.strip().lower().encode()).hexdigest()[:8]

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
    g.bind("sop", SOP); g.bind("org", ORG); g.bind("foaf", FOAF); g.bind("base", BASE); g.bind("rdfs", RDFS);g.bind("skos", SKOS)

    # --- A. INSTANCIAR O POP (SOP) ---
    metadata = data.get('metadata', {})
    pop_num = metadata.get('number') or "temp"
    pop_uri = BASE[f"pop_{pop_num}"]
    
    g.add((pop_uri, RDF.type, SOP.Sop))
    g.add((pop_uri, SOP.name, Literal(metadata.get('name', ''), datatype=XSD.string)))
    g.add((pop_uri, SOP.version, Literal(metadata.get('version', ''), datatype=XSD.string)))
    g.add((pop_uri, SOP.description, Literal(metadata.get('description', ''), datatype=XSD.string)))
    
    dates = metadata.get('dates', {})
    if dates.get('creation'):
        g.add((pop_uri, SOP.creationDate, Literal(dates['creation'], datatype=XSD.dateTime)))
    if dates.get('approval'):
        g.add((pop_uri, SOP.approvalDate, Literal(dates['approval'], datatype=XSD.date)))
    
    status_val = metadata.get('status')
    if status_val:
        g.add((pop_uri, SOP.status, SOP[status_val]))

    # --- CLASSIFICAÇÕES (SKOS) ---
    classifications = metadata.get('classifications', [])
    for class_name in classifications:
        if class_name:
            # Cria um ID estável para o conceito (ex: concept_higienizacao)
            class_id = generate_stable_id(class_name)
            class_uri = BASE[f"concept_{class_id}"]
            
            # 1. Define como um skos:Concept
            g.add((class_uri, RDF.type, SKOS.Concept))
            # 2. Adiciona o termo (prefLabel)
            g.add((class_uri, SKOS.prefLabel, Literal(class_name, datatype=XSD.string)))
            # 3. Liga o POP à classificação via Object Property
            g.add((pop_uri, SOP.classification, class_uri))
    terms = metadata.get('terms', [])
    for t in terms:
        if t.get('name'):
            # Gera ID estável para o termo para evitar duplicatas
            term_id = generate_stable_id(t['name'])
            term_uri = BASE[f"term_{term_id}"]
            
            # Define como um Conceito SKOS
            g.add((term_uri, RDF.type, SKOS.Concept))
            # Adiciona o Termo/Sigla como rótulo preferencial
            g.add((term_uri, SKOS.prefLabel, Literal(t['name'], datatype=XSD.string)))
            
            # Se houver definição, adiciona usando skos:definition
            if t.get('definition'):
                g.add((term_uri, SKOS.definition, Literal(t['definition'], datatype=XSD.string)))
                
            # LIGAÇÃO CRUCIAL: POP -> skos:Concept via sop:term
            g.add((pop_uri, SOP['term'], term_uri))


    # Organização Responsável (Usando RDFS.label para melhor interoperabilidade)
    # --- MÚLTIPLOS RESPONSÁVEIS (ORGANIZAÇÕES) ---
    for org in metadata.get('responsible', []):
        if org.get('name'):
            org_id = generate_stable_id(org['name'])
            org_uri = BASE[f"org_{org_id}"]
            g.add((org_uri, RDF.type, ORG.Organization))
            g.add((org_uri, RDFS.label, Literal(org['name'])))
            g.add((pop_uri, SOP.responsible, org_uri))

    # --- B. AGENTES (Criador, Revisor, Aprovador) ---
    agents_data = data.get('agents', {})
    # Mapeamento de listas do JSON para propriedades da Ontologia
    mapping = [
        ('creators', SOP.createdBy),
        ('checkers', SOP.checkedBy),
        ('approvers', SOP.approvedBy)
    ]


    for key, predicate in mapping:
        for agent in agents_data.get(key, []):
            if agent.get('name'):
                a_id = generate_stable_id(agent['name'])
                agent_uri = BASE[f"agent_{a_id}"]
                g.add((agent_uri, RDF.type, FOAF[agent['type']])) 
                g.add((agent_uri, FOAF.name, Literal(agent['name'])))
                g.add((pop_uri, predicate, agent_uri))

    # --- C. ETAPAS (STEPS) E LÓGICA DE FLUXO ---
    step_uris = {} 
    for i, s in enumerate(data.get('steps', [])):
        s_idx = i + 1
        step_uri = BASE[f"step_{pop_num}_{s_idx}"]
        step_uris[str(s_idx)] = step_uri
        
        g.add((step_uri, RDF.type, SOP.Step))
        g.add((step_uri, SOP.name, Literal(s['name'])))
        g.add((pop_uri, SOP.includes, step_uri))

        # Executor e Local
        for key, prop, cls_prefix in [('performer', SOP.performedBy, 'perf'), ('place', SOP.performedAt, 'place')]:
            obj = s.get(key, {})
            if obj.get('name'):
                obj_id = generate_stable_id(obj['name'])
                obj_uri = BASE[f"{cls_prefix}_{obj_id}"]
                g.add((obj_uri, RDF.type, SOP[obj['type']]))
                g.add((obj_uri, RDFS.label, Literal(obj['name'])))
                g.add((step_uri, prop, obj_uri))

        # Condições (BooleanExpression)
        logic = s.get('logic', {})
        if logic.get('preCondition'):
            pre_uri = BASE[f"pre_{generate_stable_id(logic['preCondition'])}"]
            g.add((pre_uri, RDF.type, SOP.BooleanExpression))
            g.add((pre_uri, SOP['term'], Literal(logic['preCondition'])))
            g.add((step_uri, SOP.preCondition, pre_uri))

    # Transições e Condições de Guarda
    for i, s in enumerate(data.get('steps', [])):
        logic = s.get('logic', {})
        if logic.get('targetId') and str(logic['targetId']) in step_uris:
            trans_uri = BASE[f"trans_{pop_num}_{i+1}"]
            g.add((trans_uri, RDF.type, SOP.Transition))
            g.add((trans_uri, SOP.target, step_uris[str(logic['targetId'])]))
            g.add((step_uris[str(i+1)], SOP.transition, trans_uri))
            
            if logic.get('guardCondition'):
                guard_uri = BASE[f"guard_{generate_stable_id(logic['guardCondition'])}"]
                g.add((guard_uri, RDF.type, SOP.BooleanExpression))
                g.add((guard_uri, SOP['term'], Literal(logic['guardCondition'])))
                g.add((trans_uri, SOP.guardCondition, guard_uri))

    # --- D. ITENS DO POP (SopItem) ---
    for item in data.get('items', []):
        item_id = generate_stable_id(f"{item['type']}_{item['name']}")
        item_uri = BASE[f"item_{item_id}"]
        g.add((item_uri, RDF.type, SOP[item['type']]))
        g.add((item_uri, SOP.name, Literal(item['name'])))
        g.add((item_uri, SOP.discriminator, Literal(item['discriminator'], datatype=XSD.integer)))
        g.add((pop_uri, SOP.sopItem, item_uri))

    # 1. Gera o conteúdo Turtle em memória
    rdf_content = g.serialize(format="turtle")

    # 2. Salva o arquivo físico para backup
    filename = f"pop_output_{pop_num}.ttl"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(rdf_content)
 
    # 3. Envio automático para o GraphDB
    foi_enviado = upload_to_graphdb(rdf_content, repo_id="Dissertacao_SOP")

    return jsonify({
        "message": "RDF gerado e enviado ao GraphDB!" if foi_enviado else "RDF gerado localmente, mas falhou ao enviar ao GraphDB.",
        "file": filename,
        "graphdb_status": foi_enviado
    }), 200

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
    sparql = """
    PREFIX sop: <https://purl.archive.org/sopontology#>
    SELECT ?id ?name ?status WHERE {
        ?pop a sop:Sop ;
             sop:name ?name .
        OPTIONAL { ?pop sop:status ?s . BIND(STRAFTER(STR(?s), "#") AS ?status) }
        BIND(STRAFTER(STR(?pop), "pop_") AS ?id)
    } ORDER BY ?name
    """
    result = query_graphdb(sparql)
    return jsonify(result['results']['bindings'] if result else [])

@app.route('/api/pop/<pop_id>', methods=['GET'])
def get_pop_details(pop_id):
    uri = f"<http://iff.edu.br/saeg/sopontology/pop_{pop_id}>"

    sparql = f"""
    PREFIX sop: <https://purl.archive.org/sopontology#>
    PREFIX foaf: <http://xmlns.com/foaf/0.1/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

    SELECT ?p ?o ?label ?def ?type ?disc WHERE {{
        {uri} ?p ?o .
        OPTIONAL {{ ?o rdfs:label ?label }}
        OPTIONAL {{ ?o foaf:name ?label }}
        OPTIONAL {{ ?o skos:prefLabel ?label }}
        OPTIONAL {{ ?o skos:definition ?def }}
        OPTIONAL {{ ?o a ?type }}
        OPTIONAL {{ ?o sop:discriminator ?disc }}
    }}
    """
    result = query_graphdb(sparql)
    if not result:
        return jsonify({"error": "POP não encontrado"}), 404

    data = {
        "metadata": {},
        "agents": {"responsible": [], "creators": [], "checkers": [], "approvers": []},
        "concepts": {"classifications": [], "terms": []},
        "items": [],
        "steps": []
    }

    for row in result['results']['bindings']:
        p = row['p']['value']
        o = row['o']['value']
        label = row.get('label', {}).get('value', o)

        if p == str(SOP.name):
            data["metadata"]["name"] = label
        elif p == str(SOP.version):
            data["metadata"]["version"] = label
        elif p == str(SOP.status):
            data["metadata"]["status"] = o.split("#")[-1]
        elif p == str(SOP.description):
            data["metadata"]["description"] = label

        elif p == str(SOP.responsible):
            data["agents"]["responsible"].append(label)
        elif p == str(SOP.createdBy):
            data["agents"]["creators"].append(label)
        elif p == str(SOP.checkedBy):
            data["agents"]["checkers"].append(label)
        elif p == str(SOP.approvedBy):
            data["agents"]["approvers"].append(label)

        elif p == str(SOP.classification):
            data["concepts"]["classifications"].append(label)

        elif p == str(SOP.term):
            data["concepts"]["terms"].append({
                "name": label,
                "definition": row.get('def', {}).get('value', '')
            })

        elif p == str(SOP.sopItem):
            data["items"].append({
                "name": label,
                "type": row.get('type', {}).get('value', '').split("#")[-1],
                "order": int(row.get('disc', {}).get('value', 0))
            })

        elif p == str(SOP.includes):
            data["steps"].append({
                "uri": o,
                "name": label
            })

    # Ordenações importantes
    data["items"].sort(key=lambda x: x["order"])
    return jsonify(data)

if __name__ == '__main__':
    app.run(debug=True, port=5000)