/**
 * Examina — JavaScript Vanilla 100% Nativo (Zero Dependências)
 * Interatividades, Menu Mobile, Formsets e Realces Visuais
 */

document.addEventListener('DOMContentLoaded', function () {
  'use strict';

  // -------------------------------------------------------------------------
  // 1. Mobile Menu Toggle
  // -------------------------------------------------------------------------
  const mobileToggle = document.querySelector('.mobile-toggle');
  const navLinks = document.querySelector('.nav-links');

  if (mobileToggle && navLinks) {
    mobileToggle.addEventListener('click', function () {
      const isOpen = navLinks.classList.toggle('open');
      mobileToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    });
  }

  // -------------------------------------------------------------------------
  // 1.1 Alternância de Tema (Claro/Escuro), persistida em localStorage
  // -------------------------------------------------------------------------
  const themeToggle = document.querySelector('.theme-toggle');
  if (themeToggle) {
    themeToggle.addEventListener('click', function () {
      const atual = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
      const proximo = atual === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', proximo);
      try {
        localStorage.setItem('examina-theme', proximo);
      } catch (e) {
        /* localStorage indisponível: aplica só nesta sessão */
      }
    });
  }

  // -------------------------------------------------------------------------
  // 2. Auto-dismiss de Alertas
  // -------------------------------------------------------------------------
  const alerts = document.querySelectorAll('.alert-toast');
  alerts.forEach(function (alert) {
    // Botão de fechar manual
    const closeBtn = alert.querySelector('.alert-close');
    if (closeBtn) {
      closeBtn.addEventListener('click', function () {
        dismissAlert(alert);
      });
    }

    // Auto fechar após 5 segundos
    setTimeout(function () {
      dismissAlert(alert);
    }, 5000);
  });

  function dismissAlert(alert) {
    alert.style.opacity = '0';
    alert.style.transform = 'translateY(-8px)';
    setTimeout(function () {
      alert.remove();
    }, 250);
  }

  // -------------------------------------------------------------------------
  // 3. Realce em Tempo Real da Alternativa Correta (Gabarito)
  // -------------------------------------------------------------------------
  function updateCorrectAlternativeHighlight() {
    const checkboxes = document.querySelectorAll('.alternativa-card-item input[type="checkbox"][name$="-correta"]');
    checkboxes.forEach(function (checkbox) {
      const card = checkbox.closest('.alternativa-card-item');
      if (card) {
        if (checkbox.checked) {
          card.classList.add('is-correct');
        } else {
          card.classList.remove('is-correct');
        }
      }
    });
  }

  document.addEventListener('change', function (e) {
    if (e.target.matches('input[type="checkbox"][name$="-correta"]')) {
      updateCorrectAlternativeHighlight();
    }
  });

  updateCorrectAlternativeHighlight();
});

// ---------------------------------------------------------------------------
// Página restaurada da memória pelo botão voltar (back/forward cache): recarrega
// para o servidor conferir a sessão. Sem isso, depois de "Sair" o celular podia
// mostrar a tela anterior como se o usuário ainda estivesse logado. O servidor já
// manda "no-store" nessas páginas; isto cobre navegadores que restauram mesmo assim.
// ---------------------------------------------------------------------------
window.addEventListener('pageshow', function (e) {
  if (e.persisted) {
    window.location.reload();
  }
});
