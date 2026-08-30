// Copia los assets web (index.html) al directorio www para Capacitor
const fs = require('fs');
const path = require('path');

const srcFile = path.join(__dirname, '..', '..', 'index.html');
const destDir = path.join(__dirname, 'www');
const destFile = path.join(destDir, 'index.html');

// Crear directorio www si no existe
if (!fs.existsSync(destDir)) {
    fs.mkdirSync(destDir, { recursive: true });
}

// Copiar index.html
fs.copyFileSync(srcFile, destFile);
console.log('Copiado: index.html -> www/index.html');

// Crear manifest.json para PWA
const manifest = {
    name: 'Devin Mobile',
    short_name: 'Devin',
    start_url: '/',
    display: 'standalone',
    background_color: '#0d1117',
    theme_color: '#0d1117',
    icons: []
};
fs.writeFileSync(path.join(destDir, 'manifest.json'), JSON.stringify(manifest, null, 2));
console.log('Creado: www/manifest.json');
