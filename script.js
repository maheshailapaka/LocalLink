// Local Link JavaScript

document.addEventListener('DOMContentLoaded', function() {
    // Auto-dismiss alerts after 5 seconds
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        setTimeout(() => {
            const bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        }, 5000);
    });

    // Form validation
    const forms = document.querySelectorAll('form');
    forms.forEach(form => {
        form.addEventListener('submit', function(e) {
            if (!form.checkValidity()) {
                e.preventDefault();
                e.stopPropagation();
            }
            form.classList.add('was-validated');
        });
    });

    // Set minimum date for booking to today
    const bookingDateInputs = document.querySelectorAll('input[type="datetime-local"]');
    bookingDateInputs.forEach(input => {
        const today = new Date();
        const year = today.getFullYear();
        const month = String(today.getMonth() + 1).padStart(2, '0');
        const day = String(today.getDate()).padStart(2, '0');
        const hours = String(today.getHours()).padStart(2, '0');
        const minutes = String(today.getMinutes()).padStart(2, '0');
        
        const minDateTime = `${year}-${month}-${day}T${hours}:${minutes}`;
        input.setAttribute('min', minDateTime);
    });

    // Smooth scroll to top
    const scrollToTop = document.createElement('button');
    scrollToTop.innerHTML = '<i class="bi bi-arrow-up"></i>';
    scrollToTop.className = 'btn btn-primary position-fixed bottom-0 end-0 m-4 rounded-circle';
    scrollToTop.style.display = 'none';
    scrollToTop.style.width = '50px';
    scrollToTop.style.height = '50px';
    scrollToTop.style.zIndex = '1000';
    document.body.appendChild(scrollToTop);

    window.addEventListener('scroll', () => {
        if (window.scrollY > 300) {
            scrollToTop.style.display = 'block';
        } else {
            scrollToTop.style.display = 'none';
        }
    });

    scrollToTop.addEventListener('click', () => {
        window.scrollTo({
            top: 0,
            behavior: 'smooth'
        });
    });

    // Search filter functionality
    const searchInput = document.querySelector('input[name="search"]');
    if (searchInput) {
        searchInput.addEventListener('input', function(e) {
            const searchTerm = e.target.value.toLowerCase();
            const cards = document.querySelectorAll('.card');
            
            cards.forEach(card => {
                const text = card.textContent.toLowerCase();
                if (text.includes(searchTerm)) {
                    card.style.display = 'block';
                } else {
                    card.style.display = 'none';
                }
            });
        });
    }

    // Confirm before important actions
    const deleteButtons = document.querySelectorAll('[data-confirm]');
    deleteButtons.forEach(button => {
        button.addEventListener('click', function(e) {
            if (!confirm(this.dataset.confirm)) {
                e.preventDefault();
            }
        });
    });

    // Rating star interaction
    const ratingSelects = document.querySelectorAll('select[name="rating"]');
    ratingSelects.forEach(select => {
        select.addEventListener('change', function() {
            const rating = parseInt(this.value);
            let stars = '';
            for (let i = 0; i < rating; i++) {
                stars += '⭐';
            }
            console.log('Selected rating:', stars);
        });
    });

    // Loading state for buttons
    const submitButtons = document.querySelectorAll('button[type="submit"]');
    submitButtons.forEach(button => {
        button.closest('form')?.addEventListener('submit', function() {
            button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Loading...';
            button.disabled = true;
        });
    });

    // Initialize tooltips
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // Dynamic search suggestions
    const cityInput = document.querySelector('input[name="city"]');
    if (cityInput) {
        const indianCities = ['Mumbai', 'Delhi', 'Bangalore', 'Hyderabad', 'Chennai', 'Kolkata', 'Pune', 'Ahmedabad', 'Jaipur', 'Lucknow'];
        
        cityInput.addEventListener('focus', function() {
            // Could implement autocomplete here
            console.log('City suggestions:', indianCities);
        });
    }

    // Print functionality
    window.printPage = function() {
        window.print();
    };

    // Share functionality
    window.shareProvider = function(providerName, providerUrl) {
        if (navigator.share) {
            navigator.share({
                title: `Check out ${providerName} on Local Link`,
                text: `I found this great service provider: ${providerName}`,
                url: providerUrl
            }).catch(err => console.log('Error sharing:', err));
        } else {
            // Fallback: copy to clipboard
            navigator.clipboard.writeText(providerUrl);
            alert('Link copied to clipboard!');
        }
    };
});

// Helper function to format currency
function formatCurrency(amount) {
    return new Intl.NumberFormat('en-IN', {
        style: 'currency',
        currency: 'INR'
    }).format(amount);
}

// Helper function to format date
function formatDate(dateString) {
    const options = { year: 'numeric', month: 'long', day: 'numeric' };
    return new Date(dateString).toLocaleDateString('en-IN', options);
}

// Google Maps integration (placeholder for future enhancement)
function initMap(latitude, longitude) {
    console.log('Map coordinates:', latitude, longitude);
    // Future: Initialize Google Maps here
}                     