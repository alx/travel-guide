import {registerRoot} from 'remotion';
import {RemotionRoot} from './Root';

// Load Font Awesome for the pedestrian icon
const fa = document.createElement('link');
fa.rel = 'stylesheet';
fa.href = 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css';
document.head.appendChild(fa);

registerRoot(RemotionRoot);
