#!/bin/bash
#PBS -N Sing_Q_negative_2_interp_003_lam_0.2222_mult_5_quintet
#PBS -l nodes=1:ppn=12
#PBS -o Sing_Q_negative_2_interp_003_lam_0.2222_mult_5_quintet.pbs.out
#PBS -e Sing_Q_negative_2_interp_003_lam_0.2222_mult_5_quintet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Sing_Q_negative_2_interp_003_lam_0.2222_mult_5_quintet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Sing_Q_negative_2_interp_003_lam_0.2222_mult_5_quintet.inp > Sing_Q_negative_2_interp_003_lam_0.2222_mult_5_quintet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
