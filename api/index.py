import os
import json
import firebase_admin
from firebase_admin import credentials, firestore
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta, timezone

app = Flask(__name__)
CORS(app)

# --- CONFIGURAÇÃO FIREBASE ---
FIREBASE_CONFIG = os.getenv("FIREBASE_CONFIG")
if FIREBASE_CONFIG:
    cred = credentials.Certificate(json.loads(FIREBASE_CONFIG))
else:
    cred = credentials.Certificate("serviceAccountKey.json")

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()


def get_agora_br():
    return datetime.now(timezone(timedelta(hours=-3)))


# --- LOGIN ADMINISTRATIVO MASTER (DONO DO SAAS) ---
@app.route('/api/admin/login', methods=['POST'])
def login_admin():
    dados = request.json
    senha_digitada = str(dados.get('senha', '')).strip()
    senha_mestra = os.getenv("ADMIN_PASSWORD", "admin123")

    if senha_digitada == senha_mestra:
        return jsonify({"auth": True}), 200
    return jsonify({"erro": "Senha incorreta"}), 401


# --- MANAGEMENT DAS UNIDADES ESCOLARES (CLIENTES DO SAAS) ---
@app.route('/api/clientes', methods=['GET', 'POST'])
def gerenciar_clientes():
    if request.method == 'POST':
        dados = request.json
        doc_ref = db.collection('clientes').document()
        dados['id'] = doc_ref.id
        if 'nome' in dados: 
            dados['nome_fantasia'] = dados['nome']
        doc_ref.set(dados)
        return jsonify(dados), 201

    docs = db.collection('clientes').stream()
    return jsonify([doc.to_dict() for doc in docs])


@app.route('/api/clientes/<id>', methods=['GET', 'PUT', 'DELETE'])
def detalhe_cliente(id):
    doc_ref = db.collection('clientes').document(id)
    if request.method == 'PUT':
        dados = request.json
        dados['id'] = id
        doc_ref.update(dados)
        return jsonify({"status": "atualizado"})
    if request.method == 'DELETE':
        doc_ref.delete()
        return jsonify({"status": "excluido"})

    doc = doc_ref.get()
    return jsonify(doc.to_dict()) if doc.exists else ({'erro': '404'}, 404)


# --- LOGIN DO TABLET / PAINEL OPERACIONAL GESTOR ---
@app.route('/api/clientes/login-tablet', methods=['POST'])
def login_unidade():
    try:
        dados = request.json
        cnpj_input = "".join(filter(str.isdigit, str(dados.get('cnpj', ''))))
        senha_input = str(dados.get('senha', '')).strip()

        docs = db.collection('clientes').stream()
        for doc in docs:
            c = doc.to_dict()
            cnpj_banco = "".join(filter(str.isdigit, str(c.get('cnpj', ''))))
            senha_banco = str(c.get('senha_acesso', '')).strip()

            if cnpj_banco == cnpj_input and senha_banco == senha_input:
                return jsonify({"id": doc.id, "nome": c.get('nome_fantasia', c.get('nome'))}), 200

        return jsonify({"erro": "Credenciais inválidas"}), 401
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


# --- ACADÊMICO: GESTÃO DE ALUNOS ---
@app.route('/api/alunos', methods=['POST'])
def criar_aluno():
    dados = request.json
    matricula = "".join(filter(str.isdigit, str(dados['matricula'])))
    dados['matricula'] = matricula
    
    # Define a matrícula como ID do documento para otimização de consultas
    db.collection('alunos').document(matricula).set(dados)
    return jsonify(dados), 201


@app.route('/api/alunos/unidade/<cliente_id>', methods=['GET'])
def listar_alunos(cliente_id):
    docs = db.collection('alunos').where('cliente_id', '==', cliente_id).stream()
    return jsonify([doc.to_dict() for doc in docs])


@app.route('/api/alunos/<matricula>', methods=['PUT', 'DELETE'])
def gerenciar_aluno_especifico(matricula):
    doc_ref = db.collection('alunos').document(matricula)
    if request.method == 'PUT':
        dados = request.json
        doc_ref.update(dados)
        return jsonify({"status": "atualizado"})
    if request.method == 'DELETE':
        doc_ref.delete()
        return jsonify({"status": "excluido"})


# --- CONTROLE DE FREQUÊNCIA (TRAVA DE PRESENÇA DIÁRIA UNIQUE) ---
@app.route('/api/ponto', methods=['POST'])
def registrar_ponto():
    try:
        dados = request.get_json()
        matricula = dados.get('matricula')
        
        if not matricula:
            return jsonify({"status": "erro", "mensagem": "Matrícula não fornecida."}), 400
            
        # 1. Busca o aluno para capturar Nome, Turma e Cliente_ID
        aluno_doc = db.collection('alunos').document(matricula).get()
        if not aluno_doc.exists:
            return jsonify({"status": "erro", "mensagem": "Aluno não encontrado."}), 404
            
        aluno_dados = aluno_doc.to_dict()
        
        # 2. Gera data e hora corretas do servidor
        fuso = pytz.timezone('America/Sao_Paulo')
        agora = datetime.now(fuso)
        data_hoje = agora.strftime('%Y-%m-%d')
        hora_atual = agora.strftime('%H:%M:%S')
        
        # 3. Busca TODAS as movimentações do aluno HOJE para saber o último estado
        pontos_hoje = db.collection('pontos') \
            .where('matricula', '==', matricula) \
            .where('data', '==', data_hoje) \
            .get()
            
        lista_pontos = [p.to_dict() for p in pontos_hoje]
        
        # 4. Lógica de Alternância Inteligente (Entrada / Saída)
        if len(lista_pontos) == 0:
            # Se não tem nenhum registro hoje, obrigatoriamente é ENTRADA
            proximo_tipo = "ENTRADA"
        else:
            # Ordena pelo horário para descobrir qual foi o último estado registrado
            lista_pontos.sort(key=lambda x: x.get('hora', '00:00:00'))
            ultimo_ponto = lista_pontos[-1]
            ultimo_tipo = ultimo_ponto.get('tipo', 'ENTRADA')
            
            # Se o último foi Entrada, agora registra Saída. Se foi Saída, registra Entrada de novo.
            proximo_tipo = "SAÍDA" if ultimo_tipo == "ENTRADA" else "ENTRADA"
            
        # 5. Monta o novo documento para salvar no Firestore
        novo_ponto = {
            "aluno": aluno_dados.get('nome'),
            "matricula": matricula,
            "turma": aluno_dados.get('turma', 'Não definida'),
            "cliente_id": aluno_dados.get('cliente_id'),
            "data": data_hoje,
            "hora": hora_atual,
            "tipo": proximo_tipo,
            "timestamp_servidor": firestore.SERVER_TIMESTAMP
        }
        
        # Salva o registro histórico
        db.collection('pontos').add(novo_ponto)
        
        # Retorna a resposta limpa para o Tablet renderizar na tela
        return jsonify({
            "status": "sucesso",
            "mensagem": f"{proximo_tipo} registrada com sucesso!",
            "aluno": aluno_dados.get('nome'),
            "tipo": proximo_tipo,
            "hora": hora_atual
        }), 200
        
    except Exception as e:
        return jsonify({"status": "erro", "mensagem": str(e)}), 500
        

@app.route('/api/ponto/unidade/<cliente_id>', methods=['GET'])
def historico_unidade(cliente_id):
    docs = db.collection('pontos').where('cliente_id', '==', cliente_id).get()
    lista = [d.to_dict() for d in docs]
    lista.sort(key=lambda x: x.get('timestamp_servidor', ''), reverse=True)
    return jsonify(lista)


if __name__ == '__main__':
    app.run(debug=True)
