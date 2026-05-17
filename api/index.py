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


# --- LOGIN ADMINISTRATIVO (DONO) ---
@app.route('/api/admin/login', methods=['POST'])
def login_admin():
    dados = request.json
    senha_digitada = str(dados.get('senha', '')).strip()
    # Puxa ADMIN_PASSWORD da Vercel. Se não existir, usa 'admin123'
    senha_mestra = os.getenv("ADMIN_PASSWORD", "admin123")

    if senha_digitada == senha_mestra:
        return jsonify({"auth": True}), 200
    return jsonify({"erro": "Senha incorreta"}), 401


# --- GERENCIAMENTO DE CLIENTES ---
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


# --- LOGIN DO TABLET ---
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


# --- REGISTO DE PONTO ---
@app.route('/api/ponto/registrar', methods=['POST'])
def registrar_ponto():
    dados = request.json
    cpf = "".join(filter(str.isdigit, str(dados.get('id_funcionario', ''))))
    f_ref = db.collection('funcionarios').document(cpf).get()

    if not f_ref.exists:
        return jsonify({"erro": "CPF não encontrado"}), 404

    func = f_ref.to_dict()
    agora = get_agora_br()

    # Ordenação manual no Python para evitar necessidade de índices compostos no Firebase
    docs = db.collection('pontos').where('id_funcionario', '==', cpf).get()
    pontos = [p.to_dict() for p in docs]
    pontos.sort(key=lambda x: x['timestamp_servidor'], reverse=True)

    tipo, horas = "ENTRADA", 0
    if pontos and pontos[0]['tipo'] == "ENTRADA":
        tipo = "SAÍDA"
        inicio = datetime.fromisoformat(pontos[0]['timestamp_servidor'])
        if inicio.tzinfo is None: inicio = inicio.replace(tzinfo=timezone(timedelta(hours=-3)))
        horas = round((agora - inicio).total_seconds() / 3600, 2)

    novo_ponto = {
        "id_funcionario": cpf, "funcionario": func['nome'], "id_cliente": dados.get('id_cliente'),
        "tipo": tipo, "timestamp_servidor": agora.isoformat(), "horas_trabalhadas": horas
    }
    db.collection('pontos').add(novo_ponto)
    return jsonify({"tipo": tipo, "funcionario": func['nome'], "horas": horas})


# --- FUNCIONÁRIOS ---
@app.route('/api/funcionarios', methods=['POST'])
def criar_func():
    dados = request.json
    cpf = "".join(filter(str.isdigit, str(dados['cpf'])))
    dados['cpf'] = cpf
    db.collection('funcionarios').document(cpf).set(dados)
    return jsonify(dados), 201


@app.route('/api/funcionarios/<cliente_id>', methods=['GET'])
def listar_funcs(cliente_id):
    docs = db.collection('funcionarios').where('cliente_id', '==', cliente_id).stream()
    return jsonify([doc.to_dict() for doc in docs])


@app.route('/api/ponto/funcionario/<cpf>', methods=['GET'])
def relatorio(cpf):
    docs = db.collection('pontos').where('id_funcionario', '==', cpf).get()
    lista = [d.to_dict() for d in docs]
    lista.sort(key=lambda x: x['timestamp_servidor'])
    return jsonify(lista)


if __name__ == '__main__':
    app.run(debug=True)
