# Come iniziare con NODO (Windows)

## 1. Prova la demo: 2 minuti, nessun accesso
Doppio click su **`demo-nodo.bat`**. La prima volta installa tutto da solo (servono
[Python 3.12+](https://www.python.org/downloads/) e [Node.js LTS](https://nodejs.org)). Si apre
http://127.0.0.1:8000, scheda **Notte**: trovi una notte simulata con un sito andato giù e riportato su
(rollback), una correzione pronta da pubblicare, una build rilanciata e una bozza. Premi **Pubblica**
e guarda NODO verificare la correzione.

## 2. Collega i tuoi siti
Doppio click su **`avvia-nodo.bat`**: la prima volta crea il file `apps\api\.env`. Inserisci gli accessi
seguendo **[docs/ACCESSI.md](docs/ACCESSI.md)** (Vercel, GitHub, Claude e, se vuoi, Telegram), poi chiudi
e riapri NODO. Nella scheda **Accessi** vedi subito cosa funziona. Nella scheda **Siti** premi
**Importa da Vercel**.

## 3. Lascialo lavorare
Tieni NODO acceso (e il PC sveglio): all'1:00 parte il turno di notte, alle 7:30 trovi il briefing.
Per avviarlo da solo quando accedi a Windows:
```
powershell -ExecutionPolicy Bypass -File scripts\avvio-automatico.ps1
```

Domande da fare a NODO: «cosa hai fatto stanotte?», «come stanno i siti?», «controlla i siti»,
«cosa devo approvare?». Come funziona tutto, nel dettaglio: [docs/NIGHT_SHIFT.md](docs/NIGHT_SHIFT.md).
