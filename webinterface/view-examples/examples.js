(function () {
  "use strict";

  const lorem = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Integer posuere erat a ante venenatis dapibus posuere velit aliquet.";
  const shortLorem = "Lorem ipsum dolor sit amet, consectetur adipiscing elit.";

  const definitions = [
    ["loading", "Chargement initial"],
    ["new-audio", "Nouvelle analyse — audio"],
    ["new-merged", "Nouvelle analyse — YAML fusionné"],
    ["new-zip", "Nouvelle analyse — ZIP"],
    ["help", "Aide intégrée"],
    ["upload-audio", "Transfert audio"],
    ["upload-merged", "Validation YAML"],
    ["upload-zip", "Préparation ZIP"],
    ["job-running", "Analyse en cours"],
    ["job-failed", "Analyse interrompue"],
    ["result-markdown", "Résultat Markdown"],
  ];

  const sourceOptions = (selected) => `
    <fieldset class="input-kind"><legend>Format source</legend><p class="muted">${shortLorem}</p>
      <div class="input-kind-options">
        <label><input type="radio" ${selected === "audio" ? "checked" : ""}> Pistes audio</label>
        <label><input type="radio" ${selected === "merged" ? "checked" : ""}> Transcription fusionnée</label>
        <label><input type="radio" ${selected === "zip" ? "checked" : ""}> Archive ZIP audio</label>
      </div>
    </fieldset>`;

  const newAnalysis = (kind) => {
    const source = kind === "audio"
      ? `<label class="dropzone"><strong>Pistes audio</strong><span>MP3 ou OGG — le transfert démarre dès la sélection</span><span class="demo-input">lorem-joueur.ogg · ipsum-mj.mp3</span></label>`
      : kind === "merged"
        ? `<label class="dropzone"><strong>Transcription fusionnée</strong><span>Un fichier YAML versionné</span><span class="demo-input">lorem_transcription.yaml</span></label>`
        : `<label class="dropzone"><strong>Archive ZIP audio</strong><span>Une archive contenant des pistes MP3 ou OGG</span><span class="demo-input">lorem-session.zip</span></label>`;
    return `<main class="page form-page"><p class="eyebrow">Nouvelle analyse</p><h1>Préparer une session Tara</h1><p class="lede">${lorem}</p><form>${sourceOptions(kind)}${source}
      <div class="field-grid"><label>Contexte<textarea rows="5">${lorem}</textarea></label><label>Résumés antérieurs<textarea rows="5">${shortLorem}</textarea></label></div>
      <div class="form-actions"><label>Langue<select><option>fr</option><option>en</option></select></label><button class="primary">Lancer l’analyse</button></div>
    </form></main>`;
  };

  const uploadRow = (name, person, progress, state) => `<li><div class="upload-file-heading"><strong>${name}</strong><span>${state}</span></div><label>Personne<input value="${person}"></label><progress value="${progress}" max="100"></progress><span>${progress} %</span></li>`;

  const uploadAudio = () => `<main class="page upload-page"><h1>Transfert et validation</h1><p class="lede">${lorem}</p><progress value="68" max="100"></progress><ul class="upload-list">
    ${uploadRow("lorem-joueur.ogg", "Lorem Joueur", 100, "Prêt")}
    ${uploadRow("ipsum-mj.mp3", "Ipsum MJ", 72, "Transfert")}
    ${uploadRow("dolor-invite.ogg", "Dolor Invité", 31, "Transfert")}
  </ul><button class="danger">Annuler la session</button></main>`;

  const uploadMerged = () => `<main class="page upload-page"><h1>Transfert et validation</h1><p class="lede">${lorem}</p>
    <section class="validation-details"><h2>Validation de la transcription fusionnée</h2><p>Schéma : tara.merged_transcription</p><p>Version du schéma : 26.0.1</p><p>Tokens mesurés : 12 345</p></section>
    <progress value="100" max="100"></progress><ul class="upload-list"><li><div class="upload-file-heading"><strong>lorem_transcription.yaml</strong><span>Prêt</span></div><progress value="100" max="100"></progress><span>100 %</span></li></ul></main>`;

  const uploadZip = () => `<main class="page upload-page"><h1>Transfert et validation</h1><p class="lede">${lorem}</p>
    <section class="validation-details"><h2>Pistes extraites</h2><ol class="demo-phases"><li>Transfert</li><li>Extraction</li><li aria-current="step">Validation des pistes</li><li>Préparation du lancement</li></ol><p>2 fichiers non audio ignorés.</p></section>
    <ul class="upload-list">${uploadRow("table/lorem.ogg", "Lorem", 100, "Prêt")}${uploadRow("table/ipsum.mp3", "Ipsum", 100, "Prêt")}</ul><button class="primary">Valider les associations et lancer</button></main>`;

  const stages = (active, failed) => ["Validation des entrées", "Transcription", "Préparation de la session", "Analyse narrative", "Synthèse", "Vérification", "Résultat prêt"].map((name, index) => {
    const className = failed && index === active ? "failed" : index < active ? "completed" : index === active ? "active" : "pending";
    return `<li class="${className}" ${index === active ? 'aria-current="step"' : ""}><strong>${name}</strong>${index === active && !failed ? `<span>${shortLorem}</span><progress value="42" max="100"></progress>` : ""}</li>`;
  }).join("");

  const infoPanel = () => `<aside class="info-panel"><h2>Statut</h2><dl><dt>Démarré</dt><dd>22 août 2026, 10:15</dd><dt>Estimation</dt><dd>18 min</dd><dt>Langue</dt><dd>fr</dd><dt>Rétention</dt><dd>29 août 2026, 10:15</dd></dl><div class="job-actions"><button>Copier le lien</button><button>Renouveler le lien</button><p class="muted">${shortLorem}</p></div></aside>`;

  const jobRunning = () => `<main class="page"><h1>Analyse en cours</h1><div class="badges"><span>Tentative 1</span><span>Analyse en cours</span></div>
    <section class="total-progress"><div><label>Progression totale</label><progress value="54" max="100"></progress></div><strong>54 %</strong><div><label>Progression de l’opération courante</label><progress value="42" max="100"></progress></div></section>
    <div class="job-grid"><ol class="timeline">${stages(3, false)}</ol>${infoPanel()}</div></main>`;

  const jobFailed = () => `<main class="page"><h1>Le traitement a échoué</h1><div class="failed-panel"><strong>Le serveur a interrompu le traitement.</strong><p>${lorem}</p></div><div class="badges"><span>Tentative 1</span><span>Le traitement a échoué</span></div>
    <div class="job-grid"><ol class="timeline">${stages(3, true)}</ol><aside class="info-panel"><h2>Actions</h2><div class="job-actions"><button>Copier le lien</button><button>Modifier et relancer</button><button>Renouveler le lien</button></div></aside></div></main>`;

  const resultMarkdown = () => `<main class="page result"><header class="result-banner"><div><h1>Analyse Tara terminée</h1><p>Expiration : 29 août 2026, 10:15</p></div><div><small>Coût approximatif du processus</small><strong>0,01234 €</strong></div></header><button class="summary-download">Télécharger le résumé (.md)</button>
    <section class="summary-document markdown-document" aria-label="Résumé de session"><h2>Lorem ipsum — séance 42</h2><p>${lorem}</p><h3>Événements principaux</h3><ul><li>Lorem ipsum dolor sit amet.</li><li>Consectetur adipiscing elit.</li><li><strong>Integer posuere</strong> erat a ante.</li></ul><h3>Personnages</h3><table><thead><tr><th>Nom</th><th>État</th><th>Objectif</th></tr></thead><tbody><tr><td>Lorem</td><td>Présent</td><td>Dolor sit amet</td></tr><tr><td>Ipsum</td><td>Absent</td><td>Consectetur elit</td></tr></tbody></table><blockquote><p>${shortLorem}</p></blockquote><h3>Note technique</h3><pre><code>lorem_status: "ipsum"\nnext_step: "dolor"</code></pre></section></main>`;

  const help = () => `<main class="page help-page"><p class="eyebrow">Guide d’utilisation</p><h1>Utiliser Tara en toute sécurité</h1><p class="lede">${lorem}</p><div class="help-grid">${["Sources acceptées", "Lien privé", "Reprendre un transfert", "Suivre l’analyse", "Rétention et suppression", "Données privées"].map((title) => `<section><h2>${title}</h2><p>${lorem}</p></section>`).join("")}</div><button class="primary">Préparer une analyse</button></main>`;

  const views = {
    loading: () => `<main class="loading"><img class="tara-logo tara-logo--loading" src="../frontend/src/assets/brand/tara-logo-black.png" alt="Tara"><p>Chargement de Tara — ${shortLorem}</p></main>`,
    "new-audio": () => newAnalysis("audio"),
    "new-merged": () => newAnalysis("merged"),
    "new-zip": () => newAnalysis("zip"),
    help,
    "upload-audio": uploadAudio,
    "upload-merged": uploadMerged,
    "upload-zip": uploadZip,
    "job-running": jobRunning,
    "job-failed": jobFailed,
    "result-markdown": resultMarkdown,
  };

  const gallery = document.getElementById("examples-gallery");
  if (gallery) {
    gallery.innerHTML = definitions.map(([key, title]) => `<article class="example-card"><header><h2>${title}</h2><a href="view.html?view=${key}">Ouvrir</a></header><iframe loading="lazy" title="${title}" src="view.html?view=${key}"></iframe></article>`).join("");
    return;
  }

  const root = document.getElementById("example-root");
  if (!root) return;
  const selected = new URLSearchParams(location.search).get("view") || "new-audio";
  const render = views[selected] || views["new-audio"];
  const title = definitions.find(([key]) => key === selected)?.[1] || "Exemple Tara";
  document.title = `${title} — exemple Tara`;
  if (selected === "loading") document.getElementById("example-header")?.remove();
  root.innerHTML = render();
})();
