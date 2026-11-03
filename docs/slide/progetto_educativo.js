/* Slide del Progetto Educativo 2026-2028 - Canicattini 1.
   Questo file va nella stessa cartella di index.html e dati.js.
   In dati.js basta UNA riga, dove vuoi le slide (dopo gli organi esecutivi):
       ...(window.SLIDE_PE || []),
*/
window.SLIDE_PE = [
  {
    id: "pe_apertura",
    tipo: "slide_divisore_sezione",
    titolo: "Progetto Educativo di Gruppo",
    sottotitolo: "Educare alla scelta · 2026-2028"
  },
  {
    id: "pe_cambio_date",
    tipo: "slide_cambio_date",
    titolo_barra: "ALLINEAMENTO ANNO SCOUT",
    pannello: {
      etichetta: "NOMENCLATURA",
      titolo: "Il PEG si allinea all’anno scout",
      testo: "L'anno scout prende come riferimento il secondo anno del bienno, anno 2027/2028 "
    },
    etichetta_prima: "PRIMA",
    prima: "2025 – 2027",
    etichetta_dopo: "DOPO",
    dopo: "2026 – 2028"
  },

  /* ---------- 1 slide: invariato / cambia ---------- */
  {
    id: "pe_differenze",
    tipo: "slide_differenze",
    titolo_barra: "PROGETTO EDUCATIVO 2026-2028: COSA CAMBIA",
    colonne: [
      {
        titolo: "Resta uguale", sottotitolo: "TUTTO IL RESTO", colore: "var(--g)", simbolo: "✓", larghezza: 330,
        voci: ["Premessa", "Analisi esterna", "Analisi interna – Branca L/C", "Fondamenta del progetto", "Le 3 aree, gli obiettivi e i percorsi"]
      },
      {
        titolo: "Cambia", sottotitolo: "ANALISI INTERNA", colore: "var(--y)", larghezza: 834,
        voci: ["<b>Branca E/G · il reparto:</b> tolta la composizione delle squadriglie (2 maschili e 3 femminili) e il passaggio al noviziato-clan",
          "<b>Branca E/G · i ragazzi:</b> aggiunta qualche criticità (motivazione limitata, ma buona partecipazione)",
          "<b>Branca E/G · coeducazione e fede:</b> la fede come scelta, la vita all’aria aperta",
          "<b>Branca R/S:</b> tolto il passaggio sul rinnovo della comunità e sui tanti novizi"]
      }
    ],
    nota: { etichetta: "RITOCCHI MINORI", testo: "errori lessicali e impaginazione" }
  },

  /* ---------- come era / come diventa, con il punto del documento ---------- */
  {
    id: "pe_diff_eg_reparto",
    tipo: "slide_prima_dopo",
    titolo_barra: "2026-2028 · COME ERA, COME DIVENTA",
    titolo: "Analisi interna › Branca E/G › 1ª parte: il reparto",
    prima: "Il Reparto misto “Cassarino-Ciarcià” è composto da cinque squadriglie, 2 maschili e 3 femminili. In questo nuovo anno scout abbiamo avuto il passaggio di molti ragazzi di ultimo anno al noviziato - clan e, pertanto, ci ritroviamo con un reparto molto giovane e con tanti novizi provenienti dal branco. Lo staff di reparto ha deciso di lavorare molto sul Con.Ca e sulle relazioni all’interno delle squadriglie e del reparto tutto.",
    dopo: "Il Reparto misto “Cassarino-Ciarcià” è composto da cinque squadriglie. Lo staff di reparto ha deciso di concentrarsi sul lavoro con il Con.Ca e sul rafforzamento delle relazioni all’interno delle squadriglie e dell’intero reparto.",
    etichetta_prima: "COME ERA",
    etichetta_dopo: "COME DIVENTA"
  },
  {
    id: "pe_diff_eg_ragazzi",
    tipo: "slide_prima_dopo",
    titolo_barra: "2026-2028 · COME ERA, COME DIVENTA",
    titolo: "Analisi interna › Branca E/G › 2ª parte: i ragazzi",
    prima: "In generale, i nostri ragazzi comunque sembrano abbastanza motivati e desiderosi di affrontare nuove avventure, cercando nel capo una figura di riferimento con cui confrontarsi e sentirsi compresi. Nonostante riscontrino molteplici problematiche in famiglia, scuola e nei diversi contesti ricreativi, non manca in loro la fantasia e la voglia di mettersi in gioco, dimostrando grande partecipazione durante le attività.",
    dopo: "In generale, i ragazzi mostrano una motivazione e un desiderio di avventura limitati, apparendo a volte svogliati e carenti di spirito di iniziativa, cercando nel capo una figura di riferimento con cui confrontarsi e sentirsi compresi. Nonostante riscontrino molteplici problematiche in famiglia, scuola e nei diversi contesti ricreativi, dimostrano comunque una buona partecipazione durante le attività.",
    etichetta_prima: "COME ERA",
    etichetta_dopo: "COME DIVENTA"
  },
  {
    id: "pe_diff_eg_fede",
    tipo: "slide_prima_dopo",
    titolo_barra: "2026-2028 · COME ERA, COME DIVENTA",
    titolo: "Analisi interna › Branca E/G › 3ª parte: coeducazione e fede",
    prima: "Purtroppo, si riscontra una scarsa collaborazione nella coeducazione con i genitori e con le altre realtà educative. Inoltre, dopo aver ricevuto i sacramenti della prima Comunione e della Santa Cresima, si nota un allontanamento dalla Chiesa e una minore testimonianza della propria fede. Cercheremo di lavorare sulle lacune riscontrate per una sana crescita e autonomia dei nostri ragazzi.",
    dopo: "Purtroppo, si rileva una scarsa collaborazione nella coeducazione con i genitori e con le altre realtà educative. Inoltre, dopo aver ricevuto i sacramenti della prima Comunione e della Santa Cresima, si nota un progressivo allontanamento dalla Chiesa e una minore testimonianza della propria fede. L’obbiettivo sarà lavorare sulle lacune riscontrate per favorire una sana crescita e autonomia dei ragazzi valorizzando la vita all’aria aperta secondo la metodologia della branca.",
    etichetta_prima: "COME ERA",
    etichetta_dopo: "COME DIVENTA"
  },
  {
    id: "pe_diff_rs",
    tipo: "slide_prima_dopo",
    titolo_barra: "2026-2028 · COME ERA, COME DIVENTA",
    titolo: "Analisi interna › Branca R/S › prima parte: la comunità",
    prima: "La Branca RS “Falchi Pellegrini” è composta da Clan e Noviziato, accogliendo ragazzi e ragazze che hanno intrapreso il loro cammino scout sin dai lupetti e che, pur con qualche discontinuità hanno vissuto l’esperienza scout. […] Si tratta di una comunità che nell’ultimo anno si è fortemente rinnovata, in quanto gran parte dei ragazzi vive fuori sede per motivi di studio e che si basa soprattutto su un elevato numero di novizi che ancora devono coltivare il senso di appartenenza alla comunità R/S.",
    dopo: "La Branca RS “Falchi Pellegrini” accoglie ragazzi e ragazze che hanno intrapreso il loro cammino scout sin dai lupetti e che, pur con qualche discontinuità hanno vissuto l’esperienza scout. […]",
    etichetta_prima: "COME ERA",
    etichetta_dopo: "COME DIVENTA",
    nota: "[…] = parte intermedia del testo, rimasta invariata."
  },

  /* ---------- aree e obiettivi (stesso colore per tutte e tre) ---------- */
  {
    id: "pe_area1",
    tipo: "slide_area_obiettivi",
    numero: "1", titolo: "Educare alla scelta giusta", colore: "var(--v)",
    citazione: "Ogni giorno, quello che scegli, quello che pensi e quello che fai è ciò che diventi.", autore: "Eraclito",
    obiettivi: [
      { titolo: "Valorizzare i principi dello scoutismo", percorso: "Costruire la propria vita secondo i valori proposti dallo scoutismo" },
      { titolo: "Sviluppare abilità decisionali", percorso: "Fornire strumenti per prendere decisioni" },
      { titolo: "Attuare la scelta consapevole", percorso: "Sostenere il ragazzo a raggiungere autonomamente una scelta responsabile" }
    ]
  },
  {
    id: "pe_area2",
    tipo: "slide_area_obiettivi",
    numero: "2", titolo: "Custodi del nostro tempo", colore: "var(--v)",
    citazione: "La Terra va contemplata e protetta e spetta a ciascuno di noi.", autore: "Papa Francesco",
    obiettivi: [
      { titolo: "Riscoprire le radici", percorso: "Riscoprire le nostre radici, conoscendo il nostro territorio in tutti i suoi aspetti, dall’ambiente, alla cultura, alle tradizioni" },
      { titolo: "Cambiare se stessi", percorso: "Modificare il proprio stile di vita in un’ottica di rispetto, salvaguardia e cura del Creato" }
    ]
  },
  {
    id: "pe_area3",
    tipo: "slide_area_obiettivi",
    numero: "3", titolo: "Futuri cittadini attivi", colore: "var(--v)",
    citazione: "La cittadinanza è un atteggiamento, uno stato d’animo, una convinzione che il tutto sia più grande della parte…", autore: "Robert A. Heinlein",
    obiettivi: [
      { titolo: "Io nella società d’oggi", percorso: "Educare alla conoscenza e valorizzazione del ruolo che ognuno ricopre nella realtà che lo circonda, per meglio conoscere, anche, come è organizzata la società di oggi" },
      { titolo: "Io con gli altri", percorso: "Educare all’accoglienza, integrazione ed inclusione" },
      { titolo: "Fare rete", percorso: "Condividere esperienze, intrecciare solidi rapporti con tutte le realtà e “gli attori” del nostro territorio" }
    ]
  }
];