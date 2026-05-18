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


# --- CONTROLE DE FREQUÊNCIA (ENTRADAS E SAÍDAS SINCRONIZADAS) ---
@app.route('/api/ponto/registrar', methods=['POST'])
def registrar_ponto():
    try:
        dados = request.json
        matricula = "".join(filter(str.isdigit, str(dados.get('matricula', ''))))
        id_cliente = dados.get('id_cliente')

        if not matricula or not id_cliente:
            return jsonify({"status": "erro", "mensagem": "Dados incompletos."}), 400

        aluno_ref = db.collection('alunos').document(matricula).get()
        if not aluno_ref.exists:
            return jsonify({"status": "erro", "mensagem": "Aluno não cadastrado."}), 404

        aluno_dados = aluno_ref.to_dict()

        if aluno_dados.get('cliente_id') != id_cliente:
            return jsonify({"status": "erro", "mensagem": "Aluno não pertence a esta unidade."}), 403

        agora = get_agora_br()
        data_hoje = agora.date().isoformat()
        hora_atual = agora.strftime('%H:%M:%S')

        # Busca se o aluno já possui registros no dia de hoje
        docs_hoje = db.collection('pontos') \
            .where('matricula', '==', matricula) \
            .where('data', '==', data_hoje) \
            .get()

        # --- LÓGICA DE ALTERNÂNCIA INTELIGENTE CORRIGIDA ---
        if len(docs_hoje) == 0:
            tipo_movimentacao = "ENTRADA"
        else:
            ultimas_batidas = [d.to_dict() for d in docs_hoje]
            ultimas_batidas.sort(key=lambda x: x.get('timestamp_servidor', ''), reverse=True)
            
            ultimo_tipo = ultimas_batidas[0].get('tipo', 'ENTRADA')
            
            if ultimo_tipo == "ENTRADA":
                tipo_movimentacao = "SAÍDA"
            else:
                tipo_movimentacao = "ENTRADA"

        # Estrutura salvando ambos os formatos de ID para evitar conflito com o front antigo/novo
        novo_ponto = {
            "aluno": aluno_dados.get('nome'),
            "matricula": matricula,
            "turma": aluno_dados.get('turma', 'Não definida'),
            "id_cliente": id_cliente,  
            "cliente_id": id_cliente,  
            "data": data_hoje,
            "hora": hora_atual,
            "tipo": tipo_movimentacao,     
            "timestamp_servidor": agora.isoformat()
        }

        db.collection('pontos').add(novo_ponto)

        return jsonify({
            "status": "sucesso",
            "aluno": aluno_dados.get('nome'),
            "tipo": tipo_movimentacao.lower(), # 'entrada' ou 'saída' (com acento minúsculo para casar com tablet.html)
            "hora": hora_atual
        }), 200

    except Exception as e:
        return jsonify({"status": "erro", "mensagem": str(e)}), 500


@app.route('/api/ponto/unidade/<cliente_id>', methods=['GET'])
def historico_unidade(cliente_id):
    # Busca por cliente_id para alimentar perfeitamente as tabelas do gestor.html
    docs = db.collection('pontos').where('cliente_id', '==', cliente_id).get()
    lista = [d.to_dict() for d in docs]
    lista.sort(key=lambda x: x.get('timestamp_servidor', ''), reverse=True)
    return jsonify(lista)


if __name__ == '__main__':
    app.run(debug=True)
