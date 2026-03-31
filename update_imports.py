import os
import re

mapping = {
    r'app\.services\.gemini': 'app.infrastructure.gemini',
    r'app\.services\.guardrail': 'app.application.guardrail',
    r'app\.services\.RAG\.shared\.vector_search': 'app.infrastructure.vector_search',
    r'app\.application\.rag\.shared\.vector_search': 'app.infrastructure.vector_search',
    r'app\.services\.RAG': 'app.application.rag',
    r'app\.services\.line\.message_service': 'app.infrastructure.line.message_service',
    r'app\.application\.line\.message_service': 'app.infrastructure.line.message_service',
    r'app\.services\.line\.client': 'app.infrastructure.line.client',
    r'app\.application\.line\.client': 'app.infrastructure.line.client',
    r'app\.services\.line\.shared': 'app.infrastructure.line.shared',
    r'app\.application\.line\.shared': 'app.infrastructure.line.shared',
    r'app\.services\.line': 'app.application.line',
    r'app\.services\.medical': 'app.application.medical',
    r'app\.services\.media': 'app.application.media',
    r'app\.orchestration': 'app.application.orchestration'
}

def replace_in_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    new_content = content
    for old, new in mapping.items():
        new_content = re.sub(old, new, new_content)

    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Updated {filepath}")

for root, dirs, files in os.walk('c:\\Users\\York\\CARE\\app'):
    for file in files:
        if file.endswith('.py'):
            replace_in_file(os.path.join(root, file))
