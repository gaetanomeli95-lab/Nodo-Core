# Come iniziare con NODO (Windows)

## 1. Prova la demo: 2 minuti, nessun accesso
Doppio click su **`demo-nodo.bat`**. La prima volta installa tutto da solo (servono
[Python 3.12+](https://www.python.org/downloads/) e [Node.js LTS](https://nodejs.org)). Si apre
http://127.0.0.1:8000. Apri la scheda **Notte**: trovi una notte simulata con un sito andato giù e riportato su
(rollback), una correzione pronta da pubblicare, una build rilanciata e una bozza. Premi **Pubblica**
e guarda NODO verificare la correzione.

## 2. Collega i tuoi siti
Doppio click su **`avvia-nodo.bat`**: la prima volta crea il file `apps\api\.env`. Inserisci gli accessi
seguendo **[docs/ACCESSI.md](docs/ACCESSI.md)** (Vercel, GitHub, Claude e, se vuoi, Telegram), poi chiudi
e riapri NODO. Nella scheda **Accessi** vedi subito cosa funziona. Nella scheda **Siti** premi
**Importa da Vercel**.

## 3. Lascialo lavorare (come una vera app)
Doppio click su **`crea-app-nodo.bat`**: crea la scorciatoia **NODO** sul Desktop e nel menu Start e lo fa partire da
solo ad ogni accesso a Windows, **senza finestre nere**: resta solo un'icona **N** vicino all'orologio
(verde = acceso, arancione = si sta accendendo, rossa = fermo; se cade riparte da solo). Con un clic sull'icona
lo apri come finestra app, dal menu lo riavvii o lo spegni. Il PC deve restare sveglio di notte: all'1:00 parte il
turno di notte, alle 7:30 trovi il briefing.
Se vuoi anche un vero **`NODO.exe`** da fissare alla barra delle applicazioni: doppio click su **`costruisci-exe.bat`**.
(Per togliere tutto: `powershell -ExecutionPolicy Bypass -File scripts\crea-app.ps1 -Rimuovi`.)

## 4. Accendi il cervello (con il tuo abbonamento Claude)
Installa Claude Code e fai l'accesso con il tuo account Claude Pro: due comandi, spiegati in
**[docs/CERVELLO.md](docs/CERVELLO.md)**. Da lì parli con NODO come con una persona: «com'è andata stanotte?»,
«cosa cambia in quella correzione?», «va bene, pubblicala», «sul sito di Prosperya cambia il titolo della home»,
«segnati di chiamare il commercialista venerdì». Le pubblicazioni partono solo dopo il tuo sì.

Il turno di notte nel dettaglio: [docs/NIGHT_SHIFT.md](docs/NIGHT_SHIFT.md).

## Se la voce funziona male

Chiudi e riapri NODO dopo ogni aggiornamento. Fai doppio click su **`diagnosi-nodo.bat`**: si apre
`nodo-diagnosi.json`, con versione del codice, librerie e conteggi degli errori. Puoi condividerlo insieme
all’orario della prova e a cosa è successo (non sente / non risponde / voce interrotta). Il rapporto non
contiene chiavi, registrazioni, trascrizioni o righe originali del log. Non prova da solo che la voce funzioni.
