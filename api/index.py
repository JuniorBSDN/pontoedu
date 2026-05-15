import os
import json
import firebase_admin
from firebase_admin import credentials, firestore
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta, timezone
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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


# --- FUNÇÃO DE ENVIO DE E-MAIL (SMTP) ---
def enviar_alerta_presenca(email_destino, nome_aluno, horario):
    # Nota: Use variáveis de ambiente para segurança
    smtp_server = "smtp.gmail.com"
    smtp_port = 587
    sender_email = os.getenv("EMAIL_SISTEMA", "seu-email@gmail.com")
    sender_password = os.getenv("EMAIL_SENHA", "sua-senha-app")

    msg = MIMEMultipart()
    msg['From'] = f"Gestão Escolar <{sender_email}>"
    msg['To'] = email_destino
    msg['Subject'] = f"Presença Confirmada: {nome_aluno}"

    corpo = f"""
    <html>
        <body>
            <h2 style="color: #4f46e5;">Confirmação de Frequência</h2>
            <p>Olá,</p>
            <p>Informamos que o aluno <b>{nome_aluno}</b> registou entrada na escola hoje às <b>{horario}</b>.</p>
            <hr>
            <p style="font-size: 0.8em; color: #64748b;">Este é um aviso automático do sistema PontoBack Escolar.</p>
        </body>
    </html>
    """
    msg.attach(MIMEText(corpo, 'html'))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, email_destino, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Erro ao enviar e-mail: {e}")
        return False


# --- ROTAS DE AUTENTICAÇÃO ---
@app.route('/api/login', methods=['POST'])
def login():
    dados = request.json
    login_user = dados.get('login')
    senha_user = dados.get('senha')

    user_query = db.collection('usuarios').where('login', '==', login_user).limit(1).get()

    if not user_query:
        return jsonify({"erro": "Utilizador não encontrado"}), 404

    user_data = user_query[0].to_dict()
    if user_data['senha'] == senha_user:
        return jsonify({"auth": True, "perfil": user_data['perfil'], "nome": user_data['nome']}), 200

    return jsonify({"erro": "Senha incorreta"}), 401


# --- GESTÃO DE ALUNOS E RESPONSÁVEIS ---
@app.route('/api/alunos', methods=['POST'])
def cadastrar_aluno():
    dados = request.json
    matricula = str(dados['matricula']).strip()

    # Criar Aluno
    aluno_ref = db.collection('alunos').document(matricula)
    aluno_ref.set({
        "nome": dados['nome'],
        "matricula": matricula,
        "email": dados.get('email_aluno'),
        "turma_id": dados.get('turma_id'),
        "qr_token": matricula,  # Token para o QR Code
        "cpf": dados.get('cpf'),
        "nascimento": dados.get('nascimento'),
        "sexo": dados.get('sexo'),
        "endereco": dados.get('endereco')
    })

    # Criar Responsável vinculado
    db.collection('responsaveis').add({
        "aluno_id": matricula,
        "nome": dados['responsavel_nome'],
        "email": dados['responsavel_email'],
        "telefone": dados['responsavel_tel']
    })

    return jsonify({"status": "sucesso"}), 201


@app.route('/api/alunos/<turma_id>', methods=['GET'])
def listar_alunos_turma(turma_id):
    # Lista alunos e anexa o status de presença de hoje
    hoje = get_agora_br().strftime('%Y-%m-%d')
    alunos = db.collection('alunos').where('turma_id', '==', turma_id).stream()

    lista = []
    for doc in alunos:
        aluno = doc.to_dict()
        # Verificar se tem presença hoje
        presenca = db.collection('presencas') \
            .where('aluno_id', '==', doc.id) \
            .where('data_hora', '>=', hoje).limit(1).get()

        aluno['status'] = "PRESENTE" if presenca else "FALTA"
        lista.append(aluno)

    return jsonify(lista), 200


# --- REGISTO DE PRESENÇA (TABLET) ---
@app.route('/api/presenca/registrar', methods=['POST'])
def registrar_presenca():
    dados = request.json
    token = str(dados.get('qr_token')).strip()
    dispositivo = dados.get('dispositivo', 'Portaria Central')

    # 1. Localizar Aluno pelo QR Token (Matrícula)
    aluno_query = db.collection('alunos').where('qr_token', '==', token).limit(1).get()

    if not aluno_query:
        return jsonify({"erro": "QR Code Inválido"}), 404

    aluno_doc = aluno_query[0]
    aluno_data = aluno_doc.to_dict()
    agora = get_agora_br()
    horario_str = agora.strftime('%H:%M')

    # 2. Registar na tabela de presenças
    db.collection('presencas').add({
        "aluno_id": aluno_doc.id,
        "data_hora": agora.isoformat(),
        "tipo": "ENTRADA",
        "dispositivo": dispositivo
    })

    # 3. Localizar Responsável para enviar e-mail
    resp_query = db.collection('responsaveis').where('aluno_id', '==', aluno_doc.id).limit(1).get()

    if resp_query:
        resp_data = resp_query[0].to_dict()
        enviar_alerta_presenca(resp_data['email'], aluno_data['nome'], horario_str)

    return jsonify({
        "status": "sucesso",
        "aluno": aluno_data['nome'],
        "horario": horario_str
    }), 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)