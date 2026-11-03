// =========================================================================
// DATI.JS - ASSEMBLEA DI GRUPPO
// Salva il file: la presentazione si ricostruisce da sola, con controllo ogni 2 secondi.
// =========================================================================

// COME È ORGANIZZATO IL FILE, dall'alto in basso:
// 1. DATI GENERALI: anno, data e orari.
// 2. SLIDE: l'ordine qui determina l'ordine della presentazione.
// 3. CENSIMENTI: soci censiti e presenti, usati nella slide quorum.
// 4. BILANCIO: entrate, uscite e storico avanzo.
// 5. PATRIMONIO: casse, conto corrente e beni durevoli.

// COME SI MODIFICA LA PRESENTAZIONE:
// - Per spostare una slide, taglia e incolla il suo blocco { ... }.
// - Per duplicarla, copia il blocco.
// - Per eliminarla, cancella il blocco.
// - Per saltarla senza cancellarla, aggiungi "nascosta": true.
// - Per collegarla all'ODG, assegna alla slide un "id": "nome" e inserisci "slide_collegata": "nome" nel punto dell'ODG.
// - Ogni blocco è separato dal successivo da una virgola.
// - Per andare a capo nei titoli usa <br>; per il grassetto usa <b>testo</b>.

// PARAMETRI VALIDI PER TUTTE LE SLIDE:
// - "id": nome da usare per collegare la slide, opzionale.
// - "nascosta": true per saltare la slide, opzionale.
// - "titolo_barra": testo nella barra viola in alto, opzionale dove è presente la barra.

// TIPI DI SLIDE E PARAMETRI:
// - slide_titolo: { titolo, sottotitolo }.
// - slide_divisore_sezione: { titolo, sottotitolo }; se il sottotitolo manca, mostra il titolo dell'assemblea.
// - slide_ordine_del_giorno: { punti: [ { testo, slide_collegata } ] }.
// - slide_avanzamento_odg: { completati: N }; i primi N punti sono contrassegnati come "FATTI".
// - slide_quorum_convocazioni: { convocazioni: [ { nome, orario, regola, riferimento_articolo } ] }; gli orari mostrati provengono da "assemblea".
// - slide_composizione_organo: { persone: [ { ruolo, icona, nome, nota } ], nota_generale }; "nota_generale" è opzionale.
// - slide_testo_articoli: { articoli: [ { titolo, paragrafi: [ ... ] } ] }.
// - Nei paragrafi di slide_testo_articoli, usa una stringa per il testo semplice oppure { testo, sottopunti: ["a", "b"] }; la dimensione del testo si adatta automaticamente.
// - slide_modifiche_documento: { titolo, prima, dopo, nota, etichetta_prima, etichetta_dopo }; "nota", "etichetta_prima" ed "etichetta_dopo" sono opzionali.
// - In slide_modifiche_documento, "prima" è il testo attuale a sinistra e "dopo" è il testo proposto a destra.
// - Al clic, le differenze vengono evidenziate: rosso barrato per il testo tolto, verde per il testo aggiunto.
// - Il confronto è parola per parola: "50%" e "50%," sono considerate parole diverse.
// - slide_rendiconto_entrate_uscite: nessun parametro, usa i dati di BILANCIO.
// - slide_rendiconto_scontrino_esito: nessun parametro.
// - slide_rendiconto_andamento_avanzo: nessun parametro.
// - slide_patrimonio_barre: nessun parametro, usa i dati di PATRIMONIO.
// - slide_patrimonio_ciambella: nessun parametro.
// - slide_foto_gruppo: usa il file "foto di gruppo".
// - slide_grazie: { testo }; se manca, il testo predefinito è "Grazie".

window.DATI = {
  // 1. DATI GENERALI
  "anno_esercizio": 2026,

  "assemblea": {
    "data": "20 Novembre 2026",
    "titolo": "Assemblea di Gruppo 2026",
    "prima_convocazione": "20:30",
    "seconda_convocazione": "21:00"
  },

  // 2. SLIDE: l'ordine di questa lista determina l'ordine della presentazione.
  "slide": [
    // Apertura
    {
      "tipo": "slide_titolo",
      "titolo": "Assemblea<br><b>di GRUPPO </b><br>2026",
      "sottotitolo": ""
    },

    // Ordine del giorno e quorum
    {
      "tipo": "slide_ordine_del_giorno",
      "punti": [
        { "testo": "Composizione organo assembleare", "slide_collegata": "organo" },
        { "testo": "Presentazione del Regolamento assembleare", "slide_collegata": "regolamento" },
        { "testo": "Conto Economico Consuntivo 2025-2026", "slide_collegata": "bilancio" },
        { "testo": "Elezione dell’organo esecutivo e di amministrazione del Gruppo 2026/2027", "slide_collegata": "esecutivo" },
        { "testo": "Contributo economico aggiuntivo destinato al Gruppo", "slide_collegata": "contributo" }
      ]
    },
    {
      "tipo": "slide_quorum_convocazioni",
      "convocazioni": [
        { "nome": "Prima convocazione", "orario": "20:30", "regola": "Valida con il 50% + 1 degli aventi diritto", "riferimento_articolo": "Art. 4" },
        { "nome": "Seconda convocazione", "orario": "21:00", "regola": "Valida con qualsiasi numero di presenti", "riferimento_articolo": "Art. 4" }
      ]
    },

    // Punto 1: composizione organo assembleare
    {
      "tipo": "slide_divisore_sezione",
      "id": "organo",
      "titolo": "Composizione<br>Organo Assembleare"
    },
    {
      "tipo": "slide_composizione_organo",
      "persone": [
        { "ruolo": "Presidente", "icona": "🎙️", "nome": "[NOME]", "nota": "" },
        { "ruolo": "Scrutatore", "icona": "🗳️", "nome": "[NOME 1]", "nota": "" },
        { "ruolo": "Scrutatore", "icona": "🗳️", "nome": "[NOME 2]", "nota": "" },
        { "ruolo": "Segretario", "icona": "✍️", "nome": "Matteo<br>Terranova", "nota": "Proposto dai Capi Gruppo" }
      ]
    },
    { "tipo": "slide_avanzamento_odg", "completati": 1 }, // Punto 1 fatto

    // Punto 2: regolamento assembleare
    {
      "tipo": "slide_divisore_sezione",
      "id": "regolamento",
      "titolo": "Regolamento<br>Assembleare"
    },
    {
      "tipo": "slide_testo_articoli",
      "articoli": [
        {
          "titolo": "Art. 1 – Funzioni e composizione",
          "paragrafi": [
            {
              "testo": "Le funzioni dell’Assemblea di Gruppo sono definite dagli art. 23 e 29 dello Statuto. In particolare, l’Assemblea:",
              "sottopunti": [
                "elegge la Comunità capi, esclusivamente per l’espletamento dei compiti dell’organo esecutivo e di amministrazione del livello di Gruppo, di cui all’art. 30 comma 2 dello Statuto;",
                "approva il bilancio dell’esercizio precedentemente concluso;",
                "delibera la quota associativa di Gruppo."
              ]
            },
            "L’Assemblea di Gruppo è composta da tutti i soci appartenenti al Gruppo; i soci minorenni sono rappresentati da coloro che ne esercitano la responsabilità genitoriale."
          ]
        }
      ]
    },
    {
      "tipo": "slide_testo_articoli",
      "articoli": [
        {
          "titolo": "Art. 4 – Costituzione dell’Assemblea",
          "paragrafi": [
            "L’Assemblea è validamente costituita con la presenza del 50% più uno degli aventi diritto, cioè gli associati regolarmente censiti al giorno in cui si tiene l’Assemblea.",
            "Sono considerati presenti coloro che sono iscritti e registrati personalmente presso la Segreteria dell’Assemblea.",
            "Qualora l’Assemblea non risulti validamente costituita verrà convocata nuovamente con le modalità sopra esposte nello stesso giorno, con validità indipendente dal numero degli aventi diritto presenti.",
            "L’Assemblea può essere svolta anche avvalendosi dei mezzi telematici secondo quanto previsto dall’art. 23 comma 8 dello Statuto."
          ]
        }
      ]
    },
    {
      // Due articoli brevi nella stessa slide
      "tipo": "slide_testo_articoli",
      "articoli": [
        {
          "titolo": "Art. 5 – Delega",
          "paragrafi": [
            "Non è ammessa alcuna forma di delega."
          ]
        },
        {
          "titolo": "Art. 6 – Ufficio di presidenza",
          "paragrafi": [
            {
              "testo": "All’inizio dei lavori l’Assemblea procede a:",
              "sottopunti": [
                "ratificare, con voto palese, la nomina del Presidente e di un Segretario, su proposta dei Capi Gruppo;",
                "ratificare, con voto palese, la nomina di uno o più scrutatori, su proposta del Presidente."
              ]
            },
            "I Capi Gruppo non possono far parte dell’Ufficio di Presidenza, mentre gli altri membri della Comunità capi possono ricoprire i ruoli di Segretario e scrutatore/i."
          ]
        }
      ]
    },
    {
      "tipo": "slide_testo_articoli",
      "articoli": [
        {
          "titolo": "Art. 8 – Funzioni del Segretario e verbalizzazione",
          "paragrafi": [
            "Il Segretario ha il compito di redigere il verbale che deve indicare un breve cenno dei fatti, l’enunciazione delle questioni proposte e le deliberazioni dell’Assemblea; qualora queste siano redatte in forma scritta, al verbale dovrà essere allegata copia.",
            "Ogni avente diritto può richiedere che si inserisca nel resoconto per intero una sua dichiarazione.",
            "Il verbale, firmato per presa visione dal Presidente della sessione e dai Capi Gruppo, è conservato a cura della Comunità capi, come previsto dall’art. 25 del Regolamento associativo."
          ]
        }
      ]
    },
    {
      "tipo": "slide_testo_articoli",
      "articoli": [
        {
          "titolo": "Art. 11 – Diritto di parola",
          "paragrafi": [
            "Nella discussione nessuno può prendere la parola se non dopo averla ottenuta dal Presidente.",
            "Il Presidente può altresì revocare la facoltà di parlare quando l’intervento non sia pertinente all’argomento in discussione o per necessità funzionali allo svolgimento assembleare, anche eventualmente contingentando i tempi dell’intervento.",
            "Coloro che chiedono di parlare hanno la parola – salvo diverso avviso del Presidente – nell’ordine di iscrizione."
          ]
        }
      ]
    },
    {
      "tipo": "slide_testo_articoli",
      "articoli": [
        {
          "titolo": "Art. 15 – Diritto di voto",
          "paragrafi": [
            "Ciascun membro dell’Assemblea ha diritto ad un voto.",
            "Per gli associati minorenni, il cui voto è espresso da coloro che esercitano la responsabilità genitoriale.",
            "Per gli associati minorenni, anche se presenti più persone che esercitano la responsabilità genitoriale, il voto sarà sempre uno, poiché riferito appunto al singolo socio."
          ]
        }
      ]
    },

    // Modifiche al regolamento: rosso = tolto, verde = aggiunto.
    // Per usare questa slide, imposta "nascosta": false oppure elimina la riga "nascosta".
    // Modifica "titolo", "prima" e "dopo"; copia il blocco per aggiungere altre modifiche.
    {
      "nascosta": true,
      "tipo": "slide_modifiche_documento",
      "titolo": "Art. 5 – Delega",
      "titolo_barra": "MODIFICHE AL REGOLAMENTO",
      "etichetta_prima": "TESTO ATTUALE",
      "etichetta_dopo": "TESTO PROPOSTO",
      "prima": "Non è ammessa alcuna forma di delega.",
      "dopo": "È ammessa una sola delega per ciascun avente diritto, conferita per iscritto.",
      "nota": "Clicca per evidenziare le differenze"
    },
    {
      "nascosta": true,
      "tipo": "slide_modifiche_documento",
      "titolo": "Art. 6 – Ufficio di presidenza",
      "titolo_barra": "MODIFICHE AL REGOLAMENTO",
      "etichetta_prima": "TESTO ATTUALE",
      "etichetta_dopo": "TESTO PROPOSTO",
      "prima": "I Capi Gruppo non possono far parte dell’Ufficio di Presidenza, mentre gli altri membri della Comunità capi possono ricoprire i ruoli di Segretario e scrutatore/i.",
      "dopo": "I Capi Gruppo non possono far parte dell’Ufficio di Presidenza, mentre gli altri membri della Comunità capi possono ricoprire il ruolo di Segretario.",
      "nota": "Clicca per evidenziare le differenze"
    },

    { "tipo": "slide_avanzamento_odg", "completati": 2 }, // Punto 2 fatto

    // Punto 3: bilancio consuntivo
    {
      "tipo": "slide_divisore_sezione",
      "id": "bilancio",
      "titolo": "BILANCIO<br>CONSUNTIVO"
    },
    { "tipo": "slide_rendiconto_entrate_uscite" },
    { "tipo": "slide_rendiconto_scontrino_esito" },
    { "tipo": "slide_rendiconto_andamento_avanzo" },
    { "tipo": "slide_patrimonio_barre" },
    { "tipo": "slide_patrimonio_ciambella" },
    { "tipo": "slide_foto_gruppo" },
    { "tipo": "slide_grazie" },
    { "tipo": "slide_foto_gruppo" },
    {
      "tipo": "slide_divisore_sezione",
      "titolo": "Votazione<br>Bilancio"
    },
    { "tipo": "slide_avanzamento_odg", "completati": 3 }, // Punto 3 fatto

    // Punto 4: organo esecutivo
    {
      "tipo": "slide_divisore_sezione",
      "id": "esecutivo",
      "titolo": "Organo<br>Esecutivo"
    },
    ...(window.SLIDE_PE || []),
    {
      "tipo": "slide_divisore_sezione",
      "titolo": "Votazione<br>Organo Esecutivo"
    },
    { "tipo": "slide_avanzamento_odg", "completati": 4 }, // Punto 4 fatto

    // Punto 5: contributo economico
    {
      "tipo": "slide_divisore_sezione",
      "id": "contributo",
      "titolo": "Contributo Economico<br>Integrativo di Gruppo"
    },
    {
      "tipo": "slide_divisore_sezione",
      "titolo": "Votazione<br>Contributo Economico"
    },
    { "tipo": "slide_avanzamento_odg", "completati": 5 }, // Punto 5 fatto: tutto completato

    // Chiusura
    { "tipo": "slide_chiusura", "titolo": "Assemblea conclusa", "sottotitolo": "Grazie a tutti!" },
    { "tipo": "slide_grazie" },
    { "tipo": "slide_foto_gruppo" },
    {
      "tipo": "slide_titolo",
      "titolo": "Assemblea<br><b>di GRUPPO </b><br>2026",
      "sottotitolo": ""
    }
  ],

  // 3. CENSIMENTI: dati usati dalla slide quorum.
  // "censiti_attuali" indica i soci censiti il giorno dell'assemblea.
  // "presenti_attuali" indica i presenti in sala; lascia null finché non lo sai.
  // Il quorum del 50% + 1 si calcola automaticamente.
  // I numeri sono di esempio: sostituiscili con i dati corretti.
  // Se cancelli tutto il blocco, la slide quorum mostra solo le due carte.
  "censimenti": {
    "censiti_attuali": 100,
    "presenti_attuali": null,
    "storico_per_anno": {
      "2022": { "censiti": 95, "presenti": 29 },
      "2023": { "censiti": 109, "presenti": 20 },
      "2024": { "censiti": 104, "presenti": 20 },
      "2025": { "censiti": 100, "presenti": 32 }
    }
  },

  // 4. BILANCIO.
  // "importo" è l'importo dell'anno corrente.
  // "importo_anno_precedente" è l'importo dell'anno precedente.
  // Per aggiungere una voce, copia una riga { ... }; le slide si adattano.
  // Soglie percentuali delle fasce dell'esito sulle entrate: pareggio, piccolo, medio.
  "soglie_percentuali_esito": [5, 10, 20],

  "entrate": [
    { "descrizione": "Entrate dagli associati<br>(uscite, campi e uniformi)", "importo": 9665.00, "importo_anno_precedente": 16835.20 },
    { "descrizione": "Quote associative", "importo": 4520.00, "importo_anno_precedente": 4315.00 },
    { "descrizione": "Erogazioni liberali (ristorni zona/regione, donazioni, contributi)", "importo": 32.30, "importo_anno_precedente": 700 },
    { "descrizione": "Raccolta fondi occasionali", "importo": 4607.60, "importo_anno_precedente": 2543 },
    { "descrizione": "Da rapporti bancari", "importo": 0.00, "importo_anno_precedente": 0 }
  ],

  "uscite": [
    { "descrizione": "Materie prime, sussidiarie, di consumi e di merci (beni non durevoli)", "importo": 3093.67, "importo_anno_precedente": 2104.5 },
    { "descrizione": "Servizi (spese per campo e attività, uniformi, censimenti)", "importo": 7767.59, "importo_anno_precedente": 15407.32 },
    { "descrizione": "Godimento beni terzi (Affitti)", "importo": 1500.00, "importo_anno_precedente": 2632 },
    { "descrizione": "Uscite diverse di gestione (Censimenti)", "importo": 4011.00, "importo_anno_precedente": 4044 },
    { "descrizione": "Uscite per raccolta fondi occasionali", "importo": 1109.80, "importo_anno_precedente": 250 }
  ],

  // Avanzo (+) o disavanzo (−) degli anni passati.
  // L'avanzo o disavanzo dell'anno corrente si calcola automaticamente: entrate meno uscite.
  "storico_avanzo_per_anno": {
    "2020": -713.25,
    "2021": 774.10,
    "2022": -115.99,
    "2023": 1649.34,
    "2024": -665.70,
    "2025": -45.12
  },

  // 5. PATRIMONIO.
  // Attenzione: la slide a ciambella si aspetta sempre queste sei voci, in quest'ordine:
  // 0 Beni durevoli, 1 Co.Ca., 2 L/C, 3 E/G, 4 R/S, 5 C/C.
  // Non cambiare l'ordine e non rimuovere voci: modifica solo gli importi.
  // "colore_css" indica il colore della barra; le variabili sono definite nell'HTML.
  "patrimonio": {
    "anno_precedente": 2025,
    "anno_corrente": 2026,
    "data_riferimento": "30 Settembre",
    "voci": [
      { "nome": "Beni Durevoli", "icona": "img/casa.png", "colore_css": "--vl", "importo_anno_precedente": 279, "importo_anno_corrente": 279 },
      { "nome": "Cassa di Co.Ca.", "icona": "img/coca.png", "colore_css": "--v2", "importo_anno_precedente": 0, "importo_anno_corrente": 0 },
      { "nome": "Cassa branca L/C", "icona": "img/lc.png", "colore_css": "--y", "importo_anno_precedente": 505.43, "importo_anno_corrente": 587.72 },
      { "nome": "Cassa branca E/G", "icona": "img/eg.png", "colore_css": "--g", "importo_anno_precedente": 522.22, "importo_anno_corrente": 1423.25 },
      { "nome": "Cassa branca R/S", "icona": "img/rs.png", "colore_css": "--r", "importo_anno_precedente": 56.5, "importo_anno_corrente": 97.75 },
      { "nome": "C/C di gruppo", "icona": "img/cc.png", "colore_css": "--v", "importo_anno_precedente": 1622, "importo_anno_corrente": 1790.27 }
    ]
  },

  "nota_beni_durevoli": "Beni durevoli: Stampante 279,00€ (in dote al gruppo dal bilancio Settembre 2024)"
};