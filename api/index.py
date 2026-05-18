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


# --- GERENCIAMENTO DE CLIENTES / ESCOLAS ---
@app.route('/api/clientes', methods=['GET', 'POST'])
def gerenciar_clientes():
    if request.method == 'POST':
        dados = request.json
        doc_ref = db.collection('clientes').document()
        dados['id'] = doc_ref.id
        if 'nome' in dados: dados['nome_fantasia'] = dados['nome']
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


# --- LOGIN DO TABLET / PAINEL GESTOR (POR CNPJ) ---
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


# --- GESTÃO DE ALUNOS (CADASTRO E LISTAGEM) ---
@app.route('/api/alunos', methods=['POST'])
def criar_aluno():
    dados = request.json
    matricula = "".join(filter(str.isdigit, str(dados['matricula'])))
    dados['matricula'] = matricula

    # Salva usando a matrícula como ID do documento
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


# --- REGISTRO DE PRESENÇA ESCOLAR (MÁXIMO 1 POR DIA) ---
@app.route('/api/ponto/registrar', methods=['POST'])
def registrar_ponto():
    dados = request.json
    matricula = "".join(filter(str.isdigit, str(dados.get('matricula', ''))))
    id_cliente = dados.get('id_cliente')

    if not matricula:
        return jsonify({"erro": "Matrícula inválida"}), 400

    aluno_ref = db.collection('alunos').document(matricula).get()
    if not aluno_ref.exists:
        return jsonify({"erro": "Matrícula não cadastrada"}), 404

    aluno = aluno_ref.to_dict()

    # Valida se o aluno pertence à escola que capturou o ponto
    if aluno.get('cliente_id') != id_cliente:
        return jsonify({"erro": "Aluno não pertence a esta instituição"}), 403

    agora = get_agora_br()
    hoje_str = agora.date().isoformat()

    # Bloqueia registros duplicados no mesmo dia
    docs_hoje = db.collection('pontos') \
        .where('matricula', '==', matricula) \
        .where('data', '==', hoje_str) \
        .limit(1).get()

    if docs_hoje:
        return jsonify({"erro": "Presença já registrada hoje!"}), 400

    # Gravação do log de presença escolar
    novo_ponto = {
        "matricula": matricula,
        "aluno": aluno['nome'],
        "turma": aluno.get('turma', 'Não definida'),
        "id_cliente": id_cliente,
        "timestamp_servidor": agora.isoformat(),
        "data": hoje_str,
        "hora": agora.strftime('%H:%M:%S')
    }
    db.collection('pontos').add(novo_ponto)

    return jsonify({
        "status": "success",
        "aluno": aluno['nome'],
        "turma": aluno.get('turma', 'N/A'),
        "hora": novo_ponto['hora']
    }), 200


# --- HISTÓRICO DE PRESENÇAS FILTRADO POR ESCOLA ---
@app.route('/api/ponto/unidade/<cliente_id>', methods=['GET'])
def historico_unidade(cliente_id):
    docs = db.collection('pontos').where('id_cliente', '==', cliente_id).get()
    lista = [d.to_dict() for d in docs]
    lista.sort(key=lambda x: x['timestamp_servidor'], reverse=True)
    return jsonify(lista)


if __name__ == '__main__':
    app.run(debug=True)
