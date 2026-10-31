const fs = require('fs');
const path = require('path');
fs.mkdirSync(path.join(__dirname, 'public'), { recursive: true });
fs.copyFileSync(require.resolve('@techstark/opencv-js/dist/opencv.js'), path.join(__dirname, 'public/opencv.js'));
