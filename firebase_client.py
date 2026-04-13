import os
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore

credentials_paths = [
    Path(os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', '')),
    Path('/etc/secrets/project-r3i-firebase-adminsdk.json'),
    Path(__file__).resolve().parent / 'project-r3i-firebase-adminsdk.json',
]

for cred_path in credentials_paths:
    if cred_path and cred_path.is_file():
        cred = credentials.Certificate(str(cred_path))
        firebase_admin.initialize_app(cred)
        break
else:
    raise FileNotFoundError(
        'Firebase service account JSON not found. Expected one of: ' \
        f'{credentials_paths}'
    )

db = firestore.client()