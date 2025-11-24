import json
p='/app/backup/items-backup.json'
with open(p,'r',encoding='utf-8') as f:
    data=json.load(f)
counts={}
for obj in data:
    m=obj.get('model','')
    counts[m]=counts.get(m,0)+1
print(counts)
