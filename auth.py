"""
auth.py
Autenticação de empresas (multi-tenant). Usa PBKDF2-HMAC-SHA256 para
armazenar senhas com segurança, sem depender de bibliotecas externas de
compilação nativa (evita problemas de instalação em serviços de nuvem).
"""
import hashlib
import secrets
import unicodedata

ITERACOES_PBKDF2 = 200_000


def gerar_hash_senha(senha: str, salt: str = None) -> tuple:
    """Gera o hash de uma senha. Se 'salt' não for informado, gera um novo
    (usado ao criar a conta). Retorna (hash_hex, salt_hex).

    Correção #20: a senha é normalizada em Unicode (forma NFC) antes de
    virar bytes. Sem isso, a MESMA senha visualmente idêntica (ex:
    "café123!") podia gerar hashes diferentes dependendo de como o
    sistema operacional, teclado ou navegador representa o acento
    internamente por baixo dos panos (caractere "é" precomposto vs. "e" +
    acento combinando separado) — fazendo o login falhar com uma senha que
    parece 100% certa na tela. Como verificar_senha chama esta função, a
    correção cobre os dois fluxos (cadastro e login) automaticamente, sem
    precisar mexer em mais nenhum lugar.
    """
    senha = unicodedata.normalize("NFC", senha)
    if salt is None:
        salt = secrets.token_hex(16)
    hash_senha = hashlib.pbkdf2_hmac(
        "sha256", senha.encode("utf-8"), salt.encode("utf-8"), ITERACOES_PBKDF2
    ).hex()
    return hash_senha, salt


def verificar_senha(senha_digitada: str, hash_salvo: str, salt_salvo: str) -> bool:
    """Verifica se a senha digitada bate com o hash salvo, em tempo
    constante (evita ataques de timing)."""
    hash_calculado, _ = gerar_hash_senha(senha_digitada, salt_salvo)
    return secrets.compare_digest(hash_calculado, hash_salvo)


def senha_e_forte(senha: str) -> tuple:
    """Validação de força de senha. Retorna (ok: bool, motivo: str).
    Correção #3: adicionada exigência de caractere especial.
    Antes aceitava 'Senha123' como senha forte.
    """
    if len(senha) < 8:
        return False, "A senha precisa ter pelo menos 8 caracteres."
    if senha.lower() == senha or senha.upper() == senha:
        return False, "Use letras maiúsculas e minúsculas na senha."
    if not any(c.isdigit() for c in senha):
        return False, "Inclua ao menos um número na senha."
    caracteres_especiais = set("!@#$%^&*()_+-=[]{}|;':\",./<>?")
    if not any(c in caracteres_especiais for c in senha):
        return False, "Inclua ao menos um caractere especial (!@#$%^&* etc.)."
    return True, ""
