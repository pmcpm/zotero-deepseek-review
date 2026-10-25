/* Scoped CSL adaptation for Zotero's citeproc-js engine.
 * Original implementation for this local workflow. No library item is mutated.
 */
var CodexReviewCSL = (() => {
  let installedHook = null;
  const clone = value => JSON.parse(JSON.stringify(value));
  const canonical = value => JSON.stringify(sort(value));
  function sort(value) {
    if (Array.isArray(value)) return value.map(sort);
    if (value && typeof value === 'object') {
      const out = {};
      for (const key of Object.keys(value).sort()) out[key] = sort(value[key]);
      return out;
    }
    return value;
  }
  function metadataSnapshot(data) {
    const out = clone(data);
    // Zotero sync versions, classification and child lists do not alter the
    // rendered bibliographic record. Keep actual fields including creators,
    // accessDate and Extra, whose CSL mappings can affect the English entry.
    for (const key of ['key', 'version', 'dateAdded', 'dateModified', 'collections',
      'tags', 'relations', 'attachments', 'notes', 'links', 'uri', 'libraryID',
      'itemID', 'synced']) delete out[key];
    return canonical(out);
  }
  const displayFields = new Set('title title-short container-title container-title-short collection-title original-title event-title event publisher publisher-place event-place authority jurisdiction genre medium archive archive-place archive_location number page volume issue edition chapter-number collection-number version annote abstract status dimensions scale section source'.split(' '));
  const nameFields = 'author editor translator recipient interviewer composer original-author container-author collection-editor editorial-director illustrator director reviewed-author'.split(' ');
  const dateFields = 'issued accessed original-date event-date submitted available-date'.split(' ');
  function asciiDisplayText(value) {
    return String(value).split(/((?:[a-z][a-z0-9+.-]*:\S+)|(?:\b10\.\d{4,9}\/\S+))/gi)
      .map((part,index) => index % 2 ? part : part.replace(/[\u2010-\u2015\u2212\ufe63\uff0d]/g,'-')).join('');
  }
  function normalizeDisplayItem(item) {
    const out = clone(item);
    for (const field of displayFields) if (typeof out[field] === 'string') out[field] = asciiDisplayText(out[field]);
    for (const field of nameFields) if (Array.isArray(out[field])) for (const name of out[field]) {
      if (name && typeof name === 'object') for (const key of Object.keys(name)) if (typeof name[key] === 'string') name[key] = asciiDisplayText(name[key]);
    }
    for (const field of dateFields) if (out[field] && typeof out[field] === 'object') {
      for (const key of ['raw','literal']) if (typeof out[field][key] === 'string') out[field][key] = asciiDisplayText(out[field][key]);
    }
    if (typeof out.note === 'string') out.note = out.note.split('\n').map(line => {
      const match = /^(\s*([A-Za-z][A-Za-z -]*?)\s*:\s*)(.*)$/.exec(line);
      return match && displayFields.has(match[2].trim().toLowerCase().replace(/ /g,'-')) ? match[1] + asciiDisplayText(match[3]) : line;
    }).join('\n');
    return out;
  }
  function sanitizeInlineHTML(html) {
    // citeproc accepts a small inline HTML vocabulary in item fields. Rebuild
    // tags rather than retaining user attributes or native block containers.
    const source = String(html || '').replace(/<(script|style|iframe|object|textarea)\b[^>]*>[\s\S]*?<\/\s*\1\s*>/gi,'');
    const stack = [], output = [];
    const aliases = { em: 'i', strong: 'b' };
    function decodeText(text) {
      return text.replace(/&(#x[0-9a-f]+|#\d+|amp|quot|apos|nbsp);/gi,(entity,code) => {
        const names = {amp:'&',quot:'"',apos:"'",nbsp:'\u00a0'};
        if (code[0] !== '#') return names[code.toLowerCase()] || entity;
        const point = /^#x/i.test(code) ? parseInt(code.slice(2),16) : parseInt(code.slice(1),10);
        // Preserve angle-bracket entities as text, never reconstruct a tag
        // from an escaped source string after the whitelist pass.
        return point > 0 && point <= 0x10ffff && point !== 60 && point !== 62 ? String.fromCodePoint(point) : entity;
      });
    }
    for (const token of source.split(/(<[^>]*>)/g)) {
      if (!token.startsWith('<')) { output.push(decodeText(token.replace(/</g,'&lt;'))); continue; }
      const match = /^<\s*(\/?)\s*([a-z][a-z0-9]*)\b[^>]*>$/i.exec(token);
      if (!match) { if (!/^<!--/.test(token)) output.push(token.replace(/</g,'&lt;')); continue; }
      let name = match[2].toLowerCase();name = aliases[name] || name;
      const smallCaps = name === 'span' && /\bstyle\s*=\s*["'][^"']*\bfont-variant\s*:\s*small-caps\b[^"']*["']/i.test(token);
      if (match[1]) {
        if (stack.length && stack[stack.length-1] === name) { output.push('</'+stack.pop()+'>'); }
      } else if (['i','b','sub','sup'].includes(name) || smallCaps) {
        output.push(smallCaps ? '<span style="font-variant:small-caps;">' : '<'+name+'>');stack.push(name);
      }
    }
    while (stack.length) output.push('</'+stack.pop()+'>');
    return output.join('').trim();
  }
  function isDoiURL(value) {
    let text = String(value || '').trim();
    try { text = decodeURIComponent(text); } catch (_) {}
    if (/^(?:doi\s*:\s*)?10\.\d{4,9}\/\S+/i.test(text)) return true;
    try {
      const url = new URL(text.includes('://') ? text : 'https://' + text);
      return ['doi.org', 'dx.doi.org', 'www.doi.org', 'www.dx.doi.org'].includes(url.hostname.toLowerCase().replace(/\.$/, ''));
    } catch (_) { return false; }
  }
  function filterItemData(item, policy = {}) {
    const out = clone(item);
    const resourceTypes = new Set(['webpage','post','post-weblog','dataset','software']);
    const keepResourceURL = policy.url_required === true || resourceTypes.has(out.type) ||
      /\bEB\s*\/\s*OL\b/i.test([out.genre,out.medium].filter(Boolean).join(' '));
    const resourceOnly = policy.url_policy === 'resource_only';
    const keepURL = value => !(policy.hide_doi && isDoiURL(value)) &&
      (!resourceOnly || keepResourceURL || isDoiURL(value));
    if (out.URL && !keepURL(out.URL)) delete out.URL;
    // Publication dates remain intact; retrieval dates belong to resources
    // retained for their online access, rather than ordinary journal records.
    if (resourceOnly && !keepResourceURL) delete out.accessed;
    if (typeof out.note === 'string') {
      out.note = out.note.split(/\r?\n/).filter(line => {
        const url = /^\s*URL\s*:\s*(.*)$/i.exec(line);
        if (url && !keepURL(url[1])) return false;
        return !(resourceOnly && !keepResourceURL && /^\s*(?:accessed|access[- ]date)\s*:/i.test(line));
      }).join('\n');
    }
    if (policy.hide_doi) {
      delete out.DOI;
      if (isDoiURL(out.URL)) delete out.URL;
      if (typeof out.note === 'string') {
        out.note = out.note.split(/\r?\n/).filter(line => !/^\s*DOI\s*:/i.test(line)
          && !(/^\s*URL\s*:/i.test(line) && isDoiURL(line.slice(line.indexOf(':') + 1)))).join('\n');
      }
    }
    return normalizeDisplayItem(out);
  }
  function actualItem(rawID, data) {
    try {
      if (rawID instanceof Zotero.Item) return rawID;
      const id = String(data.id ?? rawID);
      if (/^\d+$/.test(id)) return Zotero.Items.get(Number(id));
    } catch (_) {}
    return null;
  }
  function decorate(item, rawID, policy) {
    const actual = actualItem(rawID, item);
    const source = clone(item);
    // Zotero's global "include URLs of paper articles" preference can omit
    // URL/accessed before a custom style sees them. Managed task styles retain
    // meaningful links without changing that global preference or library data.
    if (actual && Zotero.Utilities?.Item?.itemToCSLJSON) {
      const complete = Zotero.Utilities.Item.itemToCSLJSON(actual);
      for (const field of ['URL', 'accessed']) {
        if (source[field] === undefined && complete[field] !== undefined) source[field] = clone(complete[field]);
      }
    }
    const itemPolicy = { ...policy, url_required: policy.url_required === true ||
      !!(actual && policy.url_required_item_keys?.includes(actual.key)) };
    const out = filterItemData(source, itemPolicy);
    if (!policy.bilingual) return out;
    const entry = actual && policy.english_entries?.[actual.key];
    // A managed bilingual style always owns annote. Remove obsolete user/test
    // values only from the render-time clone, then add a verified matching one.
    delete out.annote;
    out.note = String(out.note || '').split(/\r?\n/).filter(line => !/^annote\s*:/i.test(line)).join('\n');
    if (!entry || typeof entry.text !== 'string' || !entry.text.trim() || entry.style_fingerprint !== policy.style_fingerprint) return out;
    if (entry.item_snapshot && metadataSnapshot(actual.toJSON()) !== entry.item_snapshot) return out;
    const inline = typeof entry.inline_html === 'string' ? sanitizeInlineHTML(entry.inline_html) : '';
    out.annote = asciiDisplayText(inline || entry.text);
    out.note += (out.note ? '\n' : '') + 'annote: ' + out.annote;
    return out;
  }
  function createEngine(xml, locale = 'zh-CN', format = 'text', policy = {}, shadow = null) {
    const sys = new Zotero.Cite.System({
      automaticJournalAbbreviations: false,
      uppercaseSubtitles: /\/apa(?:$|-)/.test(policy.base_style_id || '')
    });
    const retrieve = sys.retrieveItem.bind(sys);
    if (shadow) {
      sys.retrieveItem = id => {
        if (String(id) !== String(shadow.id)) throw new Error('Unexpected English shadow item ID');
        return filterItemData(shadow, policy);
      };
    } else {
      sys.retrieveItem = id => decorate(retrieve(id), id, policy);
    }
    const engine = new Zotero.CiteProc.CSL.Engine(sys, xml, locale, true);
    engine.setOutputFormat(format);
    engine.free = () => 0;
    engine.opt.development_extensions.wrap_url_and_doi = false;
    engine.opt.development_extensions.parse_names = false;
    return engine;
  }
  function renderEnglish(xml, englishCSL, policy = {}) {
    if (!englishCSL || typeof englishCSL !== 'object' || !englishCSL.title) throw new Error('Audited English CSL title required');
    const item = clone(englishCSL);
    item.id = 'codex-review-english-shadow';
    item.language = 'en-US';
    delete item.annote;
    item.note = String(item.note || '').split(/\r?\n/).filter(line => !/^annote\s*:/i.test(line)).join('\n');
    const result = {};
    for (const format of ['text', 'html']) {
      const engine = createEngine(xml, 'en-US', format, { ...policy, bilingual: false }, item);
      try {
        engine.updateItems([item.id]);
        const bib = engine.makeBibliography();
        if (!bib || bib[1].length !== 1) throw new Error('English bibliography generation failed');
        result[format] = bib[1][0].trim();
        if (format === 'text') result[format] = result[format].replace(/\s*\r?\n\s*/g, ' ').trim();
      } finally { engine.free(); }
    }
    result.inline_html = sanitizeInlineHTML(result.html);
    return result;
  }
  function installHooks(resolvePolicy) {
    if (installedHook) throw new Error('CSL hook already installed');
    if (typeof resolvePolicy !== 'function') throw new Error('Policy resolver required');
    const original = Zotero.Style.prototype.getCiteProc;
    function wrapped(locale, format, options) {
      const policy = resolvePolicy(this.styleID);
      if (!policy) return original.call(this, locale, format, options);
      return createEngine(this.getXML(), locale || 'zh-CN', format || 'text', policy);
    }
    Zotero.Style.prototype.getCiteProc = wrapped;
    installedHook = { original, wrapped };
    return () => {
      if (!installedHook) return;
      if (Zotero.Style.prototype.getCiteProc === installedHook.wrapped) Zotero.Style.prototype.getCiteProc = installedHook.original;
      installedHook = null;
    };
  }
  return { canonical, metadataSnapshot, asciiDisplayText, normalizeDisplayItem, sanitizeInlineHTML, isDoiURL, filterItemData, createEngine, renderEnglish, installHooks };
})();
