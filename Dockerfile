FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Utilisateur sans droits : le bot ne tourne jamais en root dans le conteneur
RUN useradd --system --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin bot

WORKDIR /app

# Les dépendances d'abord : cette couche n'est reconstruite que si requirements.txt change
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Les droits des fichiers copiés dépendent de la machine d'origine (scp depuis Windows, umask...) :
# on les normalise pour que l'utilisateur « bot » puisse toujours lire le code
RUN chmod -R a+rX /app

# Dossier de la base de données : monté en volume depuis le VPS (voir compose.yml)
RUN mkdir -p /app/data && chown bot:bot /app/data

USER bot

CMD ["python", "bot.py"]
