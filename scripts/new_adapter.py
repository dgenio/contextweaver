import os

def create_adapter(name):
    adapter_path = os.path.join('adapters', f'{name}.py')
    with open(adapter_path, 'w') as f:
        f.write(f'''# adapters/{name}.py
class {name.capitalize()}Adapter:
    def __init__(self):
        pass

    def connect(self):
        pass

    def disconnect(self):
        pass

    def send(self, data):
        pass

    def receive(self):
        pass
''')

if __name__ == '__main__':
    import sys
    if len(sys.argv) != 2:
        print("Usage: python scripts/new_adapter.py <name>")
        sys.exit(1)

    name = sys.argv[1]
    create_adapter(name)