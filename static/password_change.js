'use strict';

const showPasswords = document.getElementById('show-passwords');
if (showPasswords) {
  showPasswords.addEventListener('change', function () {
    ['current-password', 'new-password', 'confirm-password'].forEach(function (id) {
      const input = document.getElementById(id);
      if (input) input.type = showPasswords.checked ? 'text' : 'password';
    });
  });
}