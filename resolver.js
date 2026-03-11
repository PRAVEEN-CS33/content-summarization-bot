const https = require('https');
https.get('https://open.spotify.com/episode/2lErM1P0U3NMHlQHOrnoKM', {
  headers: {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  }
}, (res) => {
  let body = '';
  res.on('data', chunk => body += chunk);
  res.on('end', () => {
    let tMatch = body.match(/<title>([^<]+?)<\/title>/i);
    if(tMatch) {
       let t = tMatch[1];
       console.log('Title:', t);
       let parts = t.split('|');
       if(parts.length > 1) {
           let left = parts[0];
           let nParts = left.split('-');
           console.log('Show:', nParts[nParts.length - 1].trim());
       }
    }
  });
});
