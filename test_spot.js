const https = require('https');
https.get('https://open.spotify.com/episode/2lErM1P0U3NMHlQHOrnoKM', (resp) => {
  let data = '';
  resp.on('data', (c) => data += c);
  resp.on('end', () => {
    let match = data.match(/Creator Economy Live/i);
    console.log(match !== null);
  });
});
